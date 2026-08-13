"""
====================================================================
[Nexus AI Engine] Restock 판정 그래프 (AI 자동 대체 발주 제안)

입고 검수 반려(매입 불가)로 판매 기회가 소실되거나 저재고가 감지되면, 3단계 판정 그래프를 거쳐 order_proposals 테이블에 PENDING 제안 카드를 적재한다.

  ① Collector (결정론적): 30일 출고량(InventoryLog OUTBOUND), 가용 재고(신품 virtual_stock + 중고 IN_STOCK), 반려 수량을 수집하고 안전재고 산식으로 기준 수량(baseline)을 계산한다.
  ② Restock Agent (gpt-4o-mini): 수집 데이터와 baseline을 앵커로 받아 최적 수량·긴급도·사유를 구조화 출력(with_structured_output)으로 제안한다.
  ③ Validator (결정론적 게이트): 제안 수량을 baseline 기준 상한으로 클램프. LLM 장애 시 baseline 그대로 fail-open (ai_source=FALLBACK_RULE).

[아키텍처 원칙] LLM은 "제안"까지만 한다. 실제 발주(Order AUTO_PO)와 신품 재고 편입은 관리자가 SCM 칸반에서 승인하는 시점에 POService가 집행한다. 검수 파이프라인의 판정(Agent)/집행(Worker) 분리 문법과 동일하다.
====================================================================
"""

import json
import math
from datetime import timedelta
import logging
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field
from sqlmodel import Session, select, func
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.core.settings_service import (
    DEFAULT_SAFETY_STOCK_THRESHOLD,
    SAFETY_STOCK_SETTING_KEY,
    get_int_setting,
)
from app.models.wms import (
    Book,
    InventoryLog,
    InventoryUsedItem,
    OrderProposal,
    now_kst,
)

logger = logging.getLogger(__name__)

# 발주 판단 상수 (결정론적 산식의 근거 - 문서/발표 시 그대로 인용)
LEAD_TIME_DAYS = 7      # 도매처 발주 → 입고 리드타임 가정
SAFETY_DAYS = 7         # 리드타임 외 추가 안전 버퍼 (일 단위)
WHOLESALE_RATE = 0.6    # 도매 매입가 = 정가의 60%
# po/service.py의 저재고 스캔 대상 선정 기준(SAFETY_STOCK_THRESHOLD, 당시 5)과 서로 다른 값으로 따로 노는 걸 뒤늦게 발견했다.
# 둘 다 "안전재고"라는 같은 개념을 가리켜야 하므로 system_settings 테이블의 단일 값(safety_stock_threshold)으로 통합했다 - collect_restock_context()가 매 호출마다 조회한다. GET/PUT /api/v1/admin/settings로 조회/변경.

# 사유 문장 생성+수량 제안 전용 LLM. 비용 최적화 원칙에 따라 gpt-4o-mini 고정 사용
try:
    _restock_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)
except Exception:
    _restock_llm = None


class RestockDecision(BaseModel):
    """Restock Agent의 구조화 출력 스키마 (with_structured_output 강제)."""
    reorder_quantity: int = Field(description="최적 대체 발주 수량 (양의 정수)")
    urgency: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] = Field(description="발주 시급도")
    reasoning: str = Field(description="추천 사유 2~3문장 (한국어, 입력 수치를 반드시 인용)")


# ==========================================
# ① Collector - 결정론적 데이터 수집 + 기준 수량 산식
# ==========================================

def collect_restock_context(
    db: Session,
    book: Book,
    *,
    rejected_quantity: int = 0,
    reject_reason_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    발주 판단에 필요한 수치를 DB에서 결정론적으로 수집한다.
    가용 재고는 신품(virtual_stock)과 중고(IN_STOCK LPN 수)가 분리 관리되므로 합산한다 -
    한쪽만 보면 재고가 충분한데도 긴급 발주를 내는 오판이 생긴다.
    """
    since = now_kst() - timedelta(days=30)
    outbound_sum = db.exec(
        select(func.coalesce(func.sum(InventoryLog.quantity_change), 0))
        .where(
            InventoryLog.book_id == book.id,
            InventoryLog.transaction_type == "OUTBOUND",
            InventoryLog.created_at >= since,
        )
    ).one()
    sales_30d = abs(int(outbound_sum or 0))  # OUTBOUND는 음수로 적재됨

    used_in_stock = db.exec(
        select(func.count(InventoryUsedItem.id)).where(
            InventoryUsedItem.book_id == book.id,
            InventoryUsedItem.item_status == "IN_STOCK",
        )
    ).one()
    from app.domains.inventory.service import get_new_stock_qty

    new_stock = get_new_stock_qty(db, book.id)
    current_stock = new_stock + int(used_in_stock or 0)

    min_safety_stock = get_int_setting(db, SAFETY_STOCK_SETTING_KEY, DEFAULT_SAFETY_STOCK_THRESHOLD)

    # 안전재고 산식: (리드타임+버퍼) 기간의 예상 수요를 커버할 목표 재고를 잡고, 부족분 + 이번 반려로 소실된 수량을 기준 발주량으로 삼는다.
    daily_velocity = sales_30d / 30.0
    demand_cover = max(math.ceil(daily_velocity * (LEAD_TIME_DAYS + SAFETY_DAYS)), min_safety_stock)
    baseline = max(demand_cover - current_stock, 0) + max(0, int(rejected_quantity))

    # 재고 소진 예상일 기반 긴급도 (LLM 폴백 및 프롬프트 앵커 겸용)
    days_of_stock = (current_stock / daily_velocity) if daily_velocity > 0 else float("inf")
    if current_stock <= 2 or days_of_stock < LEAD_TIME_DAYS:
        urgency = "CRITICAL"
    elif current_stock <= 5 or days_of_stock < (LEAD_TIME_DAYS + SAFETY_DAYS):
        urgency = "HIGH"
    elif baseline > 0:
        urgency = "MEDIUM"
    else:
        urgency = "LOW"

    return {
        "isbn": book.isbn,
        "title": book.title,
        "new_stock": new_stock,
        "used_in_stock": int(used_in_stock or 0),
        "current_stock": current_stock,
        "sales_velocity_30d": sales_30d,
        "days_of_stock": None if days_of_stock == float("inf") else round(days_of_stock, 1),
        "rejected_quantity": max(0, int(rejected_quantity)),
        "reject_reason_code": reject_reason_code,
        "baseline_quantity": int(baseline),
        "rule_urgency": urgency,
        "min_safety_stock": min_safety_stock,
    }


# ==========================================
# ② Restock Agent - gpt-4o-mini 수량/긴급도/사유 제안
# ==========================================

def run_restock_agent(context: Dict[str, Any]) -> tuple[Dict[str, Any], str]:
    """
    LLM에게 수집 데이터를 주고 발주 제안을 받는다. 반환: (제안 dict, ai_source).
    LLM 불가/실패 시 Collector의 결정론적 산식으로 fail-open 한다 - 서브모델이 죽어도 발주 제안 자체는 반드시 생성되어야 하기 때문이다.
    """
    fallback = _rule_based_decision(context)
    if not _restock_llm:
        return fallback, "FALLBACK_RULE"

    reject_line = (
        f"- 이번 입고 검수에서 {context['rejected_quantity']}권이 "
        f"[{context['reject_reason_code'] or '검수 반려'}] 사유로 매입 반려되어 재고 편입이 차단되었습니다."
        if context["rejected_quantity"] > 0
        else "- 이번 트리거는 저재고 스캔이며 반려 수량은 없습니다."
    )
    prompt = f"""당신은 B2B 도서 물류센터의 재고 보충(Restock) 담당 AI입니다.
아래 데이터를 근거로 신품 대체 발주 수량을 제안하세요.

[수집 데이터]
- 도서: {context['title']} (ISBN {context['isbn']})
- 최근 30일 출고(판매)량: {context['sales_velocity_30d']}권 (일평균 {round(context['sales_velocity_30d'] / 30.0, 2)}권)
- 현재 가용 재고: {context['current_stock']}권 (신품 {context['new_stock']} + 중고 {context['used_in_stock']})
- 재고 소진 예상: {context['days_of_stock'] if context['days_of_stock'] is not None else '판매 이력 없음'}일
{reject_line}
- 결정론적 안전재고 산식 기준 수량: {context['baseline_quantity']}권
  (산식: 리드타임 {LEAD_TIME_DAYS}일 + 안전버퍼 {SAFETY_DAYS}일 수요 커버 목표, 최소 안전선 {context['min_safety_stock']}권)

[규칙]
1. reorder_quantity는 기준 수량을 앵커로 삼되, 판매 추세·반려 손실을 고려해 조정하세요.
   단 기준 수량의 2배를 초과하는 제안은 시스템이 어차피 클램프하므로 넘지 마세요.
2. urgency는 재고 소진 예상일과 리드타임({LEAD_TIME_DAYS}일)의 관계로 판단하세요.
3. reasoning은 한국어 2~3문장으로, 위 수치(판매량/재고/반려)를 반드시 인용해
   관리자가 승인 여부를 판단할 근거를 서술하세요. 이모지는 쓰지 마세요.
"""
    try:
        structured = _restock_llm.with_structured_output(RestockDecision)
        decision: RestockDecision = structured.invoke([HumanMessage(content=prompt)])
        return decision.model_dump(), "LLM_GPT4O_MINI"
    except Exception as e:
        print(f"[Restock Agent] LLM 제안 실패, 결정론적 산식으로 폴백: {e}")
        return fallback, "FALLBACK_RULE"


def _rule_based_decision(context: Dict[str, Any]) -> Dict[str, Any]:
    """LLM 폴백용 결정론적 제안 (Collector 산식 그대로)."""
    qty = max(1, context["baseline_quantity"])
    reject_part = (
        f"이번 검수에서 {context['rejected_quantity']}권이 [{context['reject_reason_code'] or '검수 반려'}]로 "
        f"매입 반려되어 해당 수량의 재고 편입이 무산되었습니다. "
        if context["rejected_quantity"] > 0 else ""
    )
    reasoning = (
        f"최근 30일 출고 {context['sales_velocity_30d']}권 대비 가용 재고가 {context['current_stock']}권"
        f"(신품 {context['new_stock']}+중고 {context['used_in_stock']})입니다. "
        f"{reject_part}"
        f"리드타임 {LEAD_TIME_DAYS}일과 안전버퍼 {SAFETY_DAYS}일 수요를 커버하기 위해 "
        f"안전재고 산식 기준 {qty}권의 신품 대체 발주를 권장합니다."
    )
    return {
        "reorder_quantity": qty,
        "urgency": context["rule_urgency"],
        "reasoning": reasoning,
    }


# ==========================================
# ③ Validator
# ==========================================

def validate_decision(decision: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    LLM 제안 수량을 결정론적 상한/하한으로 강제한다. LLM이 환각으로 극단값을
    내더라도 DB에는 검증된 수치만 적재된다 (금전 관련 수치의 최종 결정권은 산식에 둔다).
    """
    baseline = max(1, context["baseline_quantity"])
    upper = max(baseline * 2, baseline + 10)
    qty = decision.get("reorder_quantity") or baseline
    try:
        qty = int(qty)
    except (TypeError, ValueError):
        qty = baseline
    qty = max(1, min(qty, upper))

    urgency = decision.get("urgency")
    if urgency not in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        urgency = context["rule_urgency"]

    reasoning = (decision.get("reasoning") or "").strip() or _rule_based_decision(context)["reasoning"]
    return {"reorder_quantity": qty, "urgency": urgency, "reasoning": reasoning}


# ==========================================
# 그래프 실행 + order_proposals 적재 (워커/스캔 공용 진입점)
# ==========================================

def generate_and_store_proposal(
    db: Session,
    book: Book,
    *,
    trigger_type: str = "INSPECTION_REJECT",
    source_job_id=None,
    rejected_quantity: int = 0,
    reject_reason_code: Optional[str] = None,
) -> Optional[OrderProposal]:
    """
    Collector → Agent → Validator를 순서대로 실행하고 order_proposals에 적재한다.
    동일 도서의 PENDING 제안이 이미 있으면 새 카드를 늘리지 않고 그 카드를 최신
    수치로 갱신한다(반려 수량은 누적) - 칸반이 중복 카드로 오염되는 것을 방지.
    발주가 필요 없는 상태(기준 수량 0, 반려 0)면 None을 반환하고 적재하지 않는다.
    """
    existing = db.exec(
        select(OrderProposal).where(
            OrderProposal.book_id == book.id,
            OrderProposal.status == "PENDING",
        )
    ).first()

    accumulated_rejected = max(0, int(rejected_quantity))
    if existing and trigger_type == "INSPECTION_REJECT":
        accumulated_rejected += max(0, int(existing.rejected_quantity or 0))

    context = collect_restock_context(
        db, book,
        rejected_quantity=accumulated_rejected,
        reject_reason_code=reject_reason_code or (existing.reject_reason_code if existing else None),
    )
    if context["baseline_quantity"] <= 0 and context["rejected_quantity"] <= 0:
        return None

    raw_decision, ai_source = run_restock_agent(context)
    decision = validate_decision(raw_decision, context)

    unit_cost = float(book.base_price or 0.0) * WHOLESALE_RATE
    if unit_cost <= 0:
        unit_cost = 25000.0 * WHOLESALE_RATE
    estimated_cost = round(unit_cost * decision["reorder_quantity"])

    if existing:
        proposal = existing
    else:
        proposal = OrderProposal(book_id=book.id)
        if trigger_type:
            proposal.trigger_type = trigger_type
        if source_job_id:
            proposal.source_job_id = source_job_id

    proposal.isbn = book.isbn
    proposal.title = book.title
    proposal.reject_reason_code = context["reject_reason_code"]
    proposal.current_stock = context["current_stock"]
    proposal.sales_velocity_30d = context["sales_velocity_30d"]
    proposal.rejected_quantity = context["rejected_quantity"]
    proposal.baseline_quantity = context["baseline_quantity"]
    proposal.proposed_quantity = decision["reorder_quantity"]
    proposal.urgency = decision["urgency"]
    proposal.reasoning = decision["reasoning"]
    proposal.ai_source = ai_source
    proposal.unit_cost = round(unit_cost)
    proposal.estimated_cost = estimated_cost
    proposal.updated_at = now_kst()
    if existing and trigger_type == "INSPECTION_REJECT":
        # 반려 이벤트가 저재고 스캔 카드를 덮어쓰는 경우 트리거를 반려로 승격
        proposal.trigger_type = "INSPECTION_REJECT"
        if source_job_id:
            proposal.source_job_id = source_job_id

    db.add(proposal)
    db.commit()
    db.refresh(proposal)

    # 새로 생성된 제안만 알림으로 올린다.(기존 카드 갱신은 소음이 되므로 제외)
    # 알림 실패가 제안 적재를 무효화해서는 안 되므로 예외는 삼킨다.
    if not existing:
        try:
            from app.domains.notifications.service import notify_restock_proposal
            notify_restock_proposal(
                book_title=book.title,
                qty=decision["reorder_quantity"],
                proposal_id=str(proposal.id),
            )
        except Exception as e:
            logger.warning(f"[Notification] 발주 제안 알림 발행 실패: {e}")

    return proposal
