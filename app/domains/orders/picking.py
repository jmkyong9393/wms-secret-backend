"""
AI 피킹 지시서(Picking Instruction) 생성 도메인 로직.

아키텍처 원칙 (순환 논리 방지):
- 재고 할당 / 피킹 순서 = 결정론적 규칙 엔진 (중고 FIFO + Zone 오름차순 동선 정렬)
- LLM(gpt-4o-mini) = 동선 요약(route_summary) / 작업자 지시문(worker_note) 내러티브 생성 전용
  → LLM 실패 시에도 지시서 발행은 항상 성공 (템플릿 폴백)
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlmodel import Session, select

from app.models.wms import (
    Book,
    Inventory,
    InventoryUsedItem,
    ItemStatusEnum,
    Location,
    Order,
    OrderItem,
    PickingInstruction,
    PickingInstructionItem,
    now_kst,
)

logger = logging.getLogger(__name__)


def publish_outbound_notification(event_type: str, category: str, title: str, description: str, extra: Optional[Dict[str, Any]] = None) -> None:
    """
    notifications:global Redis Pub/Sub 채널에 출고 파이프라인 이벤트를 발행한다.
    헤더 벨/토스트(SSE)와 worker 스캐너 화면이 실시간 수신한다. Redis 미가용 시 무시(비치명).
    """
    try:
        import json
        import redis as sync_redis
        from app.core.redis_pubsub import REDIS_URL

        event = {
            "type": event_type,
            "category": category,
            "title": title,
            "description": description,
            "time_ago": "방금 전",
            "timestamp": now_kst().isoformat(),
            **(extra or {}),
        }
        # 발행은 부가 기능 - Redis 지연/장애가 주문 파이프라인을 블로킹하지 않도록 짧은 타임아웃 강제
        client = sync_redis.Redis.from_url(
            REDIS_URL, decode_responses=True,
            socket_timeout=2, socket_connect_timeout=2,
        )
        try:
            client.publish("notifications:global", json.dumps(event, ensure_ascii=False))
        finally:
            client.close()
    except Exception as e:
        logger.warning(f"Outbound notification publish failed (non-fatal): {e}")


def generate_instruction_no(session: Session) -> str:
    """PICK-YYMMDD-#### 일련번호 생성 (당일 발행 건수 기반)"""
    prefix = f"PICK-{datetime.now().strftime('%y%m%d')}"
    todays = session.exec(
        select(PickingInstruction).where(PickingInstruction.instruction_no.startswith(prefix))
    ).all()
    return f"{prefix}-{len(todays) + 1:04d}"


def _location_of(session: Session, location_id: Optional[UUID]) -> Dict[str, str]:
    if location_id:
        loc = session.get(Location, location_id)
        if loc:
            # DB에 "A" / "Zone C" 표기가 혼재 - 동선 정렬 일관성을 위해 "Zone " 접두어 제거 정규화
            zone = (loc.zone or "A").replace("Zone", "").replace("zone", "").strip() or "A"
            return {"zone": zone, "rack": loc.rack or "01", "shelf": loc.shelf or "01"}
    return {"zone": "A", "rack": "01", "shelf": "01"}


def allocate_order_items(session: Session, order: Order) -> List[PickingInstructionItem]:
    """
    규칙 기반 재고 할당 엔진.
    - 신품 라인: books 마스터 + inventory 위치 (Zone A 신품 구역), ISBN 스캔 매칭
    - 중고 라인: IN_STOCK 상태 LPN을 FIFO(created_at 오름차순)로 개별 할당 → ALLOCATED 마킹
    할당 후 Zone > Rack > Shelf 오름차순으로 pick_seq(동선 순서)를 부여한다.
    """
    order_items = session.exec(
        select(OrderItem).where(OrderItem.order_id == order.id)
    ).all()

    draft: List[PickingInstructionItem] = []

    for oi in order_items:
        book = session.get(Book, oi.book_id)
        if not book:
            continue

        # unit_price는 주문 시점 확정가.
        # condition_pref: "NEW"=신품 강제, "USED"=중고 FIFO 강제, None=중고 우선 자동 할당
        remaining = max(1, oi.quantity)
        pref = (oi.condition_pref or "AUTO").upper()

        used_rows = []
        if pref in ("USED", "AUTO"):
            used_rows = session.exec(
                select(InventoryUsedItem)
                .where(InventoryUsedItem.book_id == book.id)
                .where(InventoryUsedItem.item_status == ItemStatusEnum.IN_STOCK.value)
                .order_by(InventoryUsedItem.created_at.asc())
            ).all()

        for used in used_rows:
            if remaining <= 0:
                break
            loc = _location_of(session, used.location_id)
            used.item_status = ItemStatusEnum.ALLOCATED.value
            used.updated_at = now_kst()
            session.add(used)
            draft.append(PickingInstructionItem(
                instruction_id=None,  # 헤더 저장 후 채움
                order_item_id=oi.id,
                book_id=book.id,
                used_item_id=used.id,
                stock_type="USED",
                lpn_barcode=used.lpn_barcode,
                isbn=book.isbn,
                title=book.title,
                quantity=1,
                zone=loc["zone"], rack=loc["rack"], shelf=loc["shelf"],
                unit_price=oi.unit_price,
            ))
            remaining -= 1

        if remaining > 0:
            # 잔여분은 신품(Zone A) 벌크 피킹 - inventory 위치가 있으면 그 위치 사용
            inv = session.exec(
                select(Inventory)
                .where(Inventory.book_id == book.id)
                .where(Inventory.quantity > 0)
                .order_by(Inventory.created_at.asc())
            ).first()
            loc = _location_of(session, inv.location_id if inv else None)
            draft.append(PickingInstructionItem(
                instruction_id=None,
                order_item_id=oi.id,
                book_id=book.id,
                stock_type="NEW",
                isbn=book.isbn,
                title=book.title,
                quantity=remaining,
                zone=loc["zone"], rack=loc["rack"], shelf=loc["shelf"],
                unit_price=oi.unit_price,
            ))

    # Zone A → B → C → D 동선 오름차순 정렬 후 피킹 순서 부여
    draft.sort(key=lambda it: (it.zone, it.rack, it.shelf, it.stock_type))
    for seq, it in enumerate(draft, start=1):
        it.pick_seq = seq
    return draft


# ==========================================
# LLM 내러티브 (실패 시 템플릿 폴백)
# ==========================================

def _fallback_narrative(items: List[PickingInstructionItem]) -> Dict[str, str]:
    zones = []
    for it in items:
        if it.zone not in zones:
            zones.append(it.zone)
    total_qty = sum(it.quantity for it in items)
    route = " → ".join(f"Zone {z}" for z in zones) if zones else "Zone A"
    return {
        "route_summary": f"{route} 순서로 총 {len(items)}개 위치에서 {total_qty}권을 피킹하세요.",
        "worker_note": "신품은 ISBN 바코드, 중고는 LPN 바코드를 스캔해 검증하세요. "
                       "양장/대형 도서는 하단, 경량 도서는 상단 적재를 권장합니다.",
    }


def generate_llm_narrative(items: List[PickingInstructionItem], customer_name: str) -> Dict[str, str]:
    """gpt-4o-mini로 피킹 동선 요약과 작업자 지시문을 생성한다. 실패 시 템플릿 폴백."""
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.prompts import ChatPromptTemplate
        from pydantic import BaseModel, Field as PydField

        class PickingNarrative(BaseModel):
            route_summary: str = PydField(description="Zone 이동 동선 중심의 2문장 이내 피킹 경로 요약 (한국어)")
            worker_note: str = PydField(description="파손 주의/스캔 방법/적재 순서 등 작업자 지시문 2문장 이내 (한국어)")

        lines = "\n".join(
            f"{it.pick_seq}. [{it.stock_type}] {it.title} x{it.quantity}권 "
            f"@ Zone {it.zone}-Rack {it.rack}-Shelf {it.shelf}"
            + (f" (LPN: {it.lpn_barcode})" if it.lpn_barcode else " (ISBN 스캔)")
            for it in items
        )
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2, timeout=8)
        prompt = ChatPromptTemplate.from_messages([
            ("system", "당신은 도서 물류센터의 피킹 작업 관제 AI입니다. "
                       "피킹 순서는 이미 규칙 엔진이 확정했으므로 변경하지 말고, "
                       "현장 작업자가 즉시 이해할 수 있는 동선 요약과 주의사항만 작성하세요."),
            ("human", "B2B 거래처: {customer}\n피킹 목록:\n{lines}"),
        ])
        chain = prompt | llm.with_structured_output(PickingNarrative)
        res: PickingNarrative = chain.invoke({"customer": customer_name, "lines": lines})
        return {"route_summary": res.route_summary, "worker_note": res.worker_note}
    except Exception as e:
        logger.warning(f"Picking narrative LLM fallback: {e}")
        return _fallback_narrative(items)


def create_picking_instruction(session: Session, order: Order, use_llm: bool = True) -> PickingInstruction:
    """주문 1건에 대한 피킹 지시서 발행 (할당 + 내러티브 + 상태 전이)"""
    items = allocate_order_items(session, order)
    if not items:
        raise ValueError("할당 가능한 재고가 없어 피킹 지시서를 발행할 수 없습니다.")

    narrative = (
        generate_llm_narrative(items, order.customer_name or "B2B 거래처")
        if use_llm else _fallback_narrative(items)
    )

    instruction = PickingInstruction(
        order_id=order.id,
        instruction_no=generate_instruction_no(session),
        status="PENDING",
        total_items=sum(it.quantity for it in items),
        picked_items=0,
        route_summary=narrative["route_summary"],
        worker_note=narrative["worker_note"],
    )
    session.add(instruction)
    session.flush()  # instruction.id 확보

    for it in items:
        it.instruction_id = instruction.id
        session.add(it)

    order.status = "PICKING"
    order.updated_at = now_kst()
    session.add(order)
    session.commit()
    session.refresh(instruction)

    # worker 실시간 알림: 신규 피킹 지시서 발행
    publish_outbound_notification(
        event_type="PICKING_INSTRUCTION_ISSUED",
        category="출고 피킹 지시",
        title=f"신규 AI 피킹 지시서 발행 [{instruction.instruction_no}]",
        description=f"{order.customer_name or 'B2B 거래처'} · 총 {instruction.total_items}권 피킹 요청 - 출고 피킹 스캐너에서 수락 후 진행하세요.",
        extra={"instruction_id": str(instruction.id), "instruction_no": instruction.instruction_no},
    )
    return instruction


def serialize_instruction(session: Session, instruction: PickingInstruction) -> Dict[str, Any]:
    """지시서 헤더 + 라인 아이템 직렬화 (프론트 공용 응답 포맷)"""
    items = session.exec(
        select(PickingInstructionItem)
        .where(PickingInstructionItem.instruction_id == instruction.id)
        .order_by(PickingInstructionItem.pick_seq.asc())
    ).all()
    order = session.get(Order, instruction.order_id)
    return {
        "id": str(instruction.id),
        "instruction_no": instruction.instruction_no,
        "order_id": str(instruction.order_id),
        "customer_name": order.customer_name if order else None,
        "order_status": order.status if order else None,
        "order_total_price": order.total_price if order else None,
        "status": instruction.status,
        "total_items": instruction.total_items,
        "picked_items": instruction.picked_items,
        "route_summary": instruction.route_summary,
        "worker_note": instruction.worker_note,
        "ai_source": instruction.ai_source,
        "accepted_by": instruction.accepted_by,
        "accepted_at": instruction.accepted_at.isoformat() if instruction.accepted_at else None,
        "box_id": instruction.box_id,
        "cushion_name": instruction.cushion_name,
        "cj_waybill_no": instruction.cj_waybill_no,
        "packed_at": instruction.packed_at.isoformat() if instruction.packed_at else None,
        "shipped_at": instruction.shipped_at.isoformat() if instruction.shipped_at else None,
        "created_at": instruction.created_at.isoformat() if instruction.created_at else None,
        "items": [
            {
                "id": str(it.id),
                "book_id": str(it.book_id),
                "used_item_id": str(it.used_item_id) if it.used_item_id else None,
                "stock_type": it.stock_type,
                "is_new": it.stock_type == "NEW",
                "lpn_barcode": it.lpn_barcode,
                "isbn": it.isbn,
                "title": it.title,
                "quantity": it.quantity,
                "picked_quantity": it.picked_quantity,
                "zone": it.zone,
                "rack": it.rack,
                "shelf": it.shelf,
                "location_label": f"{it.zone}-{it.rack}-{it.shelf}",
                "pick_seq": it.pick_seq,
                "unit_price": it.unit_price,
                "status": it.status,
                "picked_at": it.picked_at.isoformat() if it.picked_at else None,
                "picked_by": it.picked_by,
            }
            for it in items
        ],
    }
