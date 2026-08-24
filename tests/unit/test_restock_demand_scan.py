"""
발주 스캔 수요 이력 규칙 단위 테스트 (2026-08-24 개편).

1순위: 재고 0 + 출고 이력 존재(기간 무관) → 품절 복구, CRITICAL 고정, 정렬 최상단
2순위: 재고 >0 + 최근 30일 OUTBOUND + 안전선 미만
제외: 출고 이력 전무(등록만 된 책) — 단 검수 반려 트리거는 수요 신호이므로 생성 유지
"""
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

import app.ai.agents.restock as restock_mod
import app.domains.inventory.service as inv_mod
import app.domains.po.service as po_mod
from app.ai.agents.restock import collect_restock_context, generate_and_store_proposal, validate_decision
from app.domains.po.service import po_service
from app.models.wms import Book, InventoryLog, InventoryUsedItem, OrderProposal, now_kst

# JSONB(PostgreSQL 전용) 컬럼이 없는 테이블만 sqlite 인메모리에 생성한다
_TABLES = [
    Book.__table__,
    InventoryLog.__table__,
    InventoryUsedItem.__table__,
    OrderProposal.__table__,
]


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=_TABLES)
    with Session(engine) as session:
        yield session


def _make_book(db: Session, title: str) -> Book:
    book = Book(title=title, isbn=str(uuid.uuid4().int)[:13])
    db.add(book)
    db.commit()
    db.refresh(book)
    return book


def _add_outbound(db: Session, book: Book, days_ago: int, qty: int = 1) -> None:
    db.add(InventoryLog(
        transaction_type="OUTBOUND",
        book_id=book.id,
        condition_grade="NEW",
        quantity_change=-abs(qty),
        created_at=now_kst() - timedelta(days=days_ago),
    ))
    db.commit()


def test_scan_candidates_priority_and_exclusion(db, monkeypatch):
    """품절 복구가 최상단, 30일 수요 저재고가 다음, 무수요/재고충분은 제외."""
    stockout = _make_book(db, "품절복구(옛 출고)")
    low_demand = _make_book(db, "저재고+30일수요")
    no_history = _make_book(db, "무수요(등록만)")
    enough = _make_book(db, "재고충분+수요")
    pending_dup = _make_book(db, "PENDING 중복")

    _add_outbound(db, stockout, days_ago=60)     # 30일 윈도 밖 이력만 존재
    _add_outbound(db, low_demand, days_ago=10)
    _add_outbound(db, enough, days_ago=5)
    _add_outbound(db, pending_dup, days_ago=3)

    db.add(OrderProposal(book_id=pending_dup.id, isbn=pending_dup.isbn, title=pending_dup.title))
    db.commit()

    stock_map = {stockout.id: 0, low_demand.id: 2, no_history.id: 0, enough.id: 10, pending_dup.id: 1}
    monkeypatch.setattr(inv_mod, "get_new_stock_map", lambda _db: dict(stock_map))
    monkeypatch.setattr(po_mod, "get_int_setting", lambda _db, _k, _d: 5)

    called = []

    def fake_generate(_db, book, **kwargs):
        called.append(book.id)
        return SimpleNamespace(
            id=uuid.uuid4(), current_stock=0, proposed_quantity=5, urgency="CRITICAL",
        )

    monkeypatch.setattr(restock_mod, "generate_and_store_proposal", fake_generate)

    result = po_service.scan_safety_stock(db)

    assert result["status"] == "success"
    # 품절 복구(1순위)가 저재고 수요(2순위)보다 먼저 제안된다
    assert called == [stockout.id, low_demand.id]
    # 무수요·재고충분·PENDING 중복은 그래프 호출 자체가 없다
    assert no_history.id not in called
    assert enough.id not in called
    assert pending_dup.id not in called


def test_collector_flags_stockout_recovery(db, monkeypatch):
    """재고 0 + 기간 무관 출고 이력 → stockout_recovery, CRITICAL, 최소 보충선 baseline."""
    book = _make_book(db, "품절 도서")
    _add_outbound(db, book, days_ago=45)

    monkeypatch.setattr(inv_mod, "get_new_stock_qty", lambda _db, _bid: 0)
    monkeypatch.setattr(restock_mod, "get_int_setting", lambda _db, _k, _d: 5)

    ctx = collect_restock_context(db, book)

    assert ctx["has_sales_history"] is True
    assert ctx["stockout_recovery"] is True
    assert ctx["rule_urgency"] == "CRITICAL"
    assert ctx["sales_velocity_30d"] == 0    # 30일 윈도에는 수요 신호가 없다
    assert ctx["baseline_quantity"] == 5     # 수요 도서의 최소 보충선


def test_validator_pins_critical_for_stockout(db, monkeypatch):
    """품절 복구 건은 LLM이 다른 긴급도를 내도 CRITICAL로 강제된다."""
    book = _make_book(db, "품절 도서")
    _add_outbound(db, book, days_ago=45)
    monkeypatch.setattr(inv_mod, "get_new_stock_qty", lambda _db, _bid: 0)
    monkeypatch.setattr(restock_mod, "get_int_setting", lambda _db, _k, _d: 5)
    ctx = collect_restock_context(db, book)

    decision = validate_decision(
        {"reorder_quantity": 3, "urgency": "LOW", "reasoning": "테스트"}, ctx
    )
    assert decision["urgency"] == "CRITICAL"


def test_no_sales_history_skips_proposal(db, monkeypatch):
    """출고 이력 전무 + 반려 0 → 제안을 생성하지 않는다 (저재고여도)."""
    book = _make_book(db, "등록만 된 책")
    monkeypatch.setattr(inv_mod, "get_new_stock_qty", lambda _db, _bid: 1)
    monkeypatch.setattr(restock_mod, "get_int_setting", lambda _db, _k, _d: 5)
    monkeypatch.setattr(restock_mod, "_restock_llm", None)

    result = generate_and_store_proposal(db, book, trigger_type="SAFETY_STOCK")

    assert result is None
    assert db.exec(select(OrderProposal)).first() is None


def test_inspection_reject_still_creates_without_history(db, monkeypatch):
    """반려 이벤트는 수요 신호이므로 출고 이력이 없어도 제안을 생성한다 (현행 유지)."""
    book = _make_book(db, "반려 발생 도서")
    monkeypatch.setattr(inv_mod, "get_new_stock_qty", lambda _db, _bid: 1)
    monkeypatch.setattr(restock_mod, "get_int_setting", lambda _db, _k, _d: 5)
    monkeypatch.setattr(restock_mod, "_restock_llm", None)

    proposal = generate_and_store_proposal(
        db, book, trigger_type="INSPECTION_REJECT", rejected_quantity=3,
    )

    assert proposal is not None
    assert proposal.rejected_quantity == 3
    assert proposal.ai_source == "FALLBACK_RULE"
    # baseline = max(최소 보충선 5 - 재고 1, 0) + 반려 3
    assert proposal.baseline_quantity == 7
