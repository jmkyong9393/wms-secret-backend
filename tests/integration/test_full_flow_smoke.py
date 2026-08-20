# 전 기능 1회 주파 통합 스모크 — "모든 기능을 한 번씩 실제로 돌려보는" 단일 진입점.
#
# 커버 범위 (쓰기 포함, 파일 내 선언 순서대로 실행):
#   인증 → 신품 Fast-Track 입고 → 재고 조회 → 주문 생성(+AI 피킹지시서) → 피킹 수락
#   → 반품 검수 접수(Celery 경계까지) → 라벨 ZPL → 게시판 CRUD → 알림/대시보드/FDS/설정 조회
#   → 생성 데이터 전량 회수(cleanup)
#
# 경계 처리 (외부 의존은 끊고, 끊은 지점을 검증한다):
#   - 알라딘 ISBN 조회: 스텁 (외부 네트워크 비의존)
#   - AI 검수 파이프라인: process_inspection.delay 호출까지만 검증 (LLM 비용 0, 워커 비의존)
#     → 파이프라인 내부는 tests/test_ubci_matrix_equivalence.py(181건)와 단위 테스트가 담당
#   - 라벨 프린터: 응답 계약만 검증 (실기기 전송 여부는 환경 설정에 따름)
#
# 실행: .venv/Scripts/python.exe -m pytest tests/integration -q
# 전제: 로컬 스택(DB) 기동 상태. 데이터는 ITEST 태그로 생성 후 마지막 테스트가 전부 삭제한다.
import time
import uuid
from types import SimpleNamespace

import pytest
from sqlmodel import Session, delete, select

from app.db.session import engine
from app.models.wms import (
    Book,
    Inventory,
    InventoryLog,
    LabelPrintJob,
    Order,
    OrderItem,
    PickingInstruction,
    PickingInstructionItem,
    ReturnJob,
)

API = "/api/v1"
RUN_TAG = f"ITEST{int(time.time())}"
FAKE_ISBN = f"979{int(time.time()) % 10_000_000_000:010d}"

STATE: dict = {}


# ── 1. 인증 ────────────────────────────────────────────────────────────────

def test_01_auth_me_and_unauthorized(client, master_headers):
    r = client.get(f"{API}/auth/me", headers=master_headers)
    assert r.status_code == 200
    assert r.json().get("role") in ("MASTER", "ADMIN")

    # 인증 없는 쓰기는 차단되어야 한다 (검수 접수 인증 누락 사고의 회귀 방지)
    r = client.post(f"{API}/returns/inspections", json={})
    assert r.status_code in (401, 403)


# ── 2. 입고 (신품 Fast-Track) ──────────────────────────────────────────────

def test_02_fasttrack_inbound(client, master_headers, monkeypatch):
    async def _no_external_meta(isbn):
        return {}

    # 가짜 ISBN이라 알라딘 실조회는 의미가 없다 - 외부 네트워크를 끊는다.
    monkeypatch.setattr(
        "app.domains.inbound.router.lookup_book_by_isbn", _no_external_meta
    )

    r = client.post(
        f"{API}/inbound/fasttrack",
        headers=master_headers,
        json={"isbn": FAKE_ISBN, "title": f"{RUN_TAG} 통합테스트 도서", "qty": 2},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "SUCCESS"
    assert body["added_qty"] == 2
    assert body["zone"].startswith("A")  # 신품존
    STATE["book_id"] = body["book_id"]


def test_03_inventory_reflects_inbound(client, master_headers):
    r = client.get(
        f"{API}/inbound/book-lookup",
        headers=master_headers,
        params={"isbn": FAKE_ISBN},
    )
    assert r.status_code == 200, r.text
    # 방금 입고한 도서가 원장에서 조회되어야 한다
    assert FAKE_ISBN in r.text


# ── 3. 주문 → 피킹 지시서 ─────────────────────────────────────────────────

def test_04_order_with_auto_picking(client, master_headers):
    r = client.post(
        f"{API}/orders/create-with-items",
        headers=master_headers,
        json={
            "customer_name": f"{RUN_TAG} 통합테스트 지점",
            "order_type": "B2B_ORDER",
            "items": [{"id": f"NEW-BOOK-{STATE['book_id']}", "quantity": 1}],
            "auto_picking_instruction": True,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["picking_instruction"] is not None
    assert body["pricing"]["final_price"] >= 0
    STATE["order_id"] = body["order_id"]
    STATE["instruction_id"] = body["picking_instruction"]["id"]


def test_05_picking_accept_and_detail(client, master_headers, worker_user):
    iid = STATE["instruction_id"]
    r = client.post(
        f"{API}/orders/picking-instructions/{iid}/accept",
        headers=master_headers,
        json={"worker_id": worker_user.employee_id},
    )
    assert r.status_code == 200, r.text

    r = client.get(f"{API}/orders/picking-instructions/{iid}", headers=master_headers)
    assert r.status_code == 200
    assert r.json()["id"] == iid


def test_06_order_read_endpoints(client, master_headers):
    for path in ("/orders/available-books", "/orders/outbound-summary"):
        r = client.get(f"{API}{path}", headers=master_headers)
        assert r.status_code == 200, f"{path}: {r.text[:200]}"


# ── 4. 반품 검수 접수 (AI 파이프라인 경계까지) ─────────────────────────────

def test_07_return_inspection_dispatch(client, master_headers, monkeypatch):
    import sys

    dispatched = []

    # 서비스가 함수 내부에서 `from app.worker.tasks import ...` 하므로 (celery 지연 로드),
    # 모듈 자체를 스텁으로 주입해야 가로챌 수 있다. 워커 실모듈은 torch/cv2를 끌어와
    # 호스트 venv에서 임포트 자체가 무겁다는 점에서도 이 방식이 맞다.
    def _stub(job_id, **kw):
        dispatched.append(str(job_id))

    _stub.delay = lambda job_id, **kw: dispatched.append(str(job_id))
    monkeypatch.setitem(
        sys.modules, "app.worker.tasks", SimpleNamespace(process_inspection=_stub)
    )

    from app.models.wms import Location

    with Session(engine) as s:
        loc = s.exec(select(Location)).first()
    assert loc is not None, "로케이션 시드가 없음"

    r = client.post(
        f"{API}/returns/inspections",
        headers=master_headers,
        json={
            "book_id": STATE["book_id"],
            "location_id": str(loc.id),
            "image_urls": [f"https://example.invalid/{RUN_TAG}.jpg"],
        },
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    STATE["return_job_id"] = job_id
    # 접수가 워커 큐 경계까지 정확히 도달했는지 - 여기서부터는 AI 파이프라인 소관
    assert dispatched == [str(job_id)]

    r = client.get(f"{API}/returns/inspections", headers=master_headers)
    assert r.status_code == 200
    assert str(job_id) in r.text


# ── 5. 라벨 출력 (ZPL 생성 계약) ───────────────────────────────────────────

def test_08_label_print_contract(client, master_headers, worker_user, monkeypatch):
    # 실프린터(LAN 소켓) 비의존 - 코드에 이미 있는 개발용 스위치로 전송만 끈다.
    # ZPL 생성·검증·이력 기록 경로는 전부 실제로 탄다.
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "LABEL_PRINTER_ENABLED", False)

    r = client.post(
        f"{API}/labels/print",
        headers=master_headers,
        json={
            "lpn": f"LPN-{RUN_TAG}",
            "mode": "LPN",
            "book_title": f"{RUN_TAG} 통합테스트 도서",
            "isbn": FAKE_ISBN,
            "worker_id": worker_user.employee_id,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 실기기 전송 여부는 환경 의존 - 응답 계약(sent/skipped 필드)만 검증한다
    assert "sent" in body and "skipped" in body


# ── 6. 게시판 CRUD ─────────────────────────────────────────────────────────

def test_09_board_full_crud(client, master_headers):
    r = client.post(
        f"{API}/board/posts",
        headers=master_headers,
        json={
            "category": "GENERAL",
            "title": f"{RUN_TAG} 통합테스트 게시글",
            "content": "통합 스모크 테스트가 작성한 글입니다. 자동 삭제됩니다.",
        },
    )
    assert r.status_code == 201, r.text
    post_id = r.json()["id"]

    r = client.patch(
        f"{API}/board/posts/{post_id}",
        headers=master_headers,
        json={"content": "수정본"},
    )
    assert r.status_code == 200

    r = client.post(
        f"{API}/board/posts/{post_id}/comments",
        headers=master_headers,
        json={"content": f"{RUN_TAG} 댓글"},
    )
    assert r.status_code in (200, 201), r.text
    comment_id = r.json()["id"]

    assert client.delete(
        f"{API}/board/comments/{comment_id}", headers=master_headers
    ).status_code in (200, 204)
    assert client.delete(
        f"{API}/board/posts/{post_id}", headers=master_headers
    ).status_code in (200, 204)


# ── 7. 조회 계열 (알림·대시보드·FDS·설정·입고이력) ─────────────────────────

@pytest.mark.parametrize(
    "path",
    [
        "/notifications",
        "/dashboard/kpi",
        "/dashboard/inspection-breakdown",
        "/dashboard/charts",
        "/dashboard/ai-quality",
        "/fds/summary",
        "/fds/reports",
        "/admin/settings",
        "/inbound/history",
    ],
)
def test_10_read_endpoints(client, master_headers, path):
    r = client.get(f"{API}{path}", headers=master_headers)
    assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"


# ── 8. 생성 데이터 전량 회수 ───────────────────────────────────────────────

def test_99_cleanup(client, master_headers):
    # 피킹 지시서는 API로 회수 (원장 정합 처리 포함)
    if STATE.get("instruction_id"):
        client.delete(
            f"{API}/orders/picking-instructions/{STATE['instruction_id']}",
            headers=master_headers,
        )

    with Session(engine) as s:
        if STATE.get("order_id"):
            oid = uuid.UUID(STATE["order_id"])
            for pi in s.exec(
                select(PickingInstruction).where(PickingInstruction.order_id == oid)
            ).all():
                s.exec(delete(PickingInstructionItem).where(
                    PickingInstructionItem.instruction_id == pi.id))
                s.delete(pi)
            s.exec(delete(OrderItem).where(OrderItem.order_id == oid))
            order = s.get(Order, oid)
            if order:
                s.delete(order)

        if STATE.get("return_job_id"):
            job = s.get(ReturnJob, uuid.UUID(str(STATE["return_job_id"])))
            if job:
                s.delete(job)

        if STATE.get("book_id"):
            bid = uuid.UUID(STATE["book_id"])
            s.exec(delete(InventoryLog).where(InventoryLog.book_id == bid))
            s.exec(delete(Inventory).where(Inventory.book_id == bid))
            book = s.get(Book, bid)
            if book:
                s.delete(book)

        s.exec(delete(LabelPrintJob).where(LabelPrintJob.lpn == f"LPN-{RUN_TAG}"))
        s.commit()

        # 회수 검증 - 태그 잔재 0건
        assert s.exec(select(Book).where(Book.isbn == FAKE_ISBN)).first() is None
