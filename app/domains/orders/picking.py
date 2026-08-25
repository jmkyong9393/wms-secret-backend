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
    Notification,
    Order,
    OrderItem,
    OrderProposal,
    PickingInstruction,
    PickingInstructionItem,
    ReturnJob,
    now_kst,
)

logger = logging.getLogger(__name__)


# 이벤트 종류별 수신 대상. 지정 없으면 전체 공개(관리자+작업자 모두 수신) - INSPECTION_DONE과
# 동일 관례. WAYBILL_ISSUED만 "포장하세요"라는 작업 지시라 WORKER 전용으로 좁힌다.
_OUTBOUND_TARGET_ROLE: Dict[str, str] = {
    "PICKING_INSTRUCTION_ISSUED": "WORKER",
    "WAYBILL_ISSUED": "WORKER",
}


def publish_outbound_notification(
    event_type: str,
    category: str,
    title: str,
    description: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    출고 파이프라인(피킹 완료/송장 발급/최종 출고) 이벤트를 알림으로 발행한다.

    종전에는 Redis notifications:global 채널에 직접 publish만 하고
    notifications DB 테이블에는 저장하지 않았다. 그 결과 화면 이동으로 SSE 연결이 끊기는
    순간(출고 흐름은 관제 화면 -> 스캐너 화면 등 여러 페이지를 오가므로 거의 매번 발생)
    발행된 이벤트가 영구 소실됐고, GET /api/v1/notifications는 DB만 읽으므로 출고를 끝까지
    진행해도 알림 패널이 계속 비어 있었다. category 인자는 notifications.service의
    _TYPE_PRESETS가 event_type 기준으로 이미 확정하므로 여기서는 쓰지 않는다(호출부 호환을
    위해 파라미터만 유지) - 문구가 호출부마다 달라지는 것을 막기 위한 서비스 설계 원칙과 일치.
    """
    from app.domains.notifications.service import notify_outbound_event

    try:
        notify_outbound_event(
            event_type=event_type,
            title=title,
            description=description,
            instruction_id=(extra or {}).get("instruction_id"),
            target_role=_OUTBOUND_TARGET_ROLE.get(event_type),
        )
    except Exception as e:
        logger.warning(f"Outbound notification publish failed (non-fatal): {e}")


def generate_instruction_no(session: Session) -> str:
    """
    PICK-YYMMDD-#### 일련번호 생성 (당일 최대 번호 + 1).

    건수를 세면 삭제로 생긴 빈자리 때문에 이미 쓰인 번호가 재발급되어
    instruction_no unique 제약에 걸린다 (0001·0002 중 0001 삭제 시 다음이 0002).

    날짜는 KST 기준이다. 컨테이너 TZ가 UTC라 datetime.now()를 쓰면 KST 00~09시에
    발행한 지시서가 전날 번호를 받는다 (실측: PICK-260809-0001이 08-10 01:25 발행).
    """
    prefix = f"PICK-{now_kst().strftime('%y%m%d')}"
    todays = session.exec(
        select(PickingInstruction).where(
            PickingInstruction.instruction_no.startswith(prefix)
        )
    ).all()
    max_seq = 0
    for ins in todays:
        try:
            max_seq = max(max_seq, int(ins.instruction_no.rsplit("-", 1)[-1]))
        except (ValueError, AttributeError):
            continue
    return f"{prefix}-{max_seq + 1:04d}"


def _unpad(value: Optional[str], default: str) -> str:
    """
    위치 좌표를 무패딩 정본으로 정규화한다 (PickingInstructionItem 모델 주석 규정: 예 A-1-1).
    "01" 같은 패딩 표기가 섞이면 같은 칸이 화면마다 다른 문자열로 보인다.
    """
    s = str(value or "").strip()
    if not s:
        return default
    # "01" -> "1". 모두 0인 값("0", "00")은 유효한 칸이 아니므로 기본값으로 되돌린다.
    return s.lstrip("0") or default


def _fallback_location_for_book(book: Optional[Book], grade: str) -> Dict[str, str]:
    """
    재고 행이 없어 실제 적치 위치를 알 수 없을 때 쓸 위치.

    종전에는 `{"zone":"A","rack":"01","shelf":"01"}`을 하드코딩해
    **창고에 존재하지도 않는 A-01-01을 지시서에 박아 넣었다**(실측: 배포 DB의 실제 위치는
    전부 A-1-1·C-1-4 같은 무패딩 표기이고 "01"은 단 한 건도 없다). 작업자는 없는 칸으로
    안내받고, 화면상으로는 정상 지시서처럼 보여 실패가 드러나지 않았다.

    이제 입고 시점과 **같은 3차원 알고리즘**(등급→존 / 카테고리→랙 / 판형→선반)으로
    "이 책이 놓일 자리"를 산출한다. 재고가 채워지면 실제로 배정될 칸과 일치하므로,
    지어낸 좌표가 아니라 규칙에서 유도된 좌표가 된다.
    """
    from app.domains.inventory.service import recommend_optimal_warehouse_zone

    zone, rack, shelf = recommend_optimal_warehouse_zone(
        grade=grade,
        category=(book.category_type if book else None) or "IT/컴퓨터",
        base_price=(book.base_price if book else None) or 20000.0,
        standard_size=(book.standard_size if book else None),
    )
    return {"zone": zone, "rack": rack, "shelf": shelf}


def _location_of(
    session: Session,
    location_id: Optional[UUID],
    *,
    book: Optional[Book] = None,
    grade: str = "NEW",
) -> Dict[str, str]:
    """실제 적치 위치를 돌려준다. 없으면 3차원 알고리즘으로 유도한 자리로 대체한다."""
    if location_id:
        loc = session.get(Location, location_id)
        if loc:
            # DB에 "A" / "Zone C" 표기가 혼재 - 동선 정렬 일관성을 위해 "Zone " 접두어 제거 정규화
            zone = (loc.zone or "A").replace("Zone", "").replace(
                "zone", ""
            ).strip() or "A"
            return {
                "zone": zone,
                "rack": _unpad(loc.rack, "1"),
                "shelf": _unpad(loc.shelf, "1"),
            }
    return _fallback_location_for_book(book, grade)


def allocate_order_items(
    session: Session, order: Order
) -> List[PickingInstructionItem]:
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
            # 주문이 특정 LPN을 지목했으면 그 개체를 그대로 집는다. 중고는 LPN마다
            # 실물·등급·가격이 다르므로 같은 책의 다른 개체로 바꿔치면 안 된다
            # (주문 화면에서 고른 책과 지시서에 찍힌 바코드가 어긋나 스캔이 실패한다).
            pinned = (
                session.get(InventoryUsedItem, oi.used_item_id)
                if oi.used_item_id
                else None
            )
            if (
                pinned is not None
                and pinned.item_status == ItemStatusEnum.IN_STOCK.value
            ):
                used_rows = [pinned]
            else:
                if oi.used_item_id and pinned is not None:
                    logger.warning(
                        "주문이 지목한 LPN을 쓸 수 없어 FIFO로 대체한다 "
                        f"(order_item={oi.id}, lpn={pinned.lpn_barcode}, status={pinned.item_status})"
                    )
                # 지목이 없는 주문(구 데이터)이나 그 사이 팔린 경우에만 FIFO로 대체한다.
                used_rows = session.exec(
                    select(InventoryUsedItem)
                    .where(InventoryUsedItem.book_id == book.id)
                    .where(
                        InventoryUsedItem.item_status == ItemStatusEnum.IN_STOCK.value
                    )
                    .order_by(InventoryUsedItem.created_at.asc())
                ).all()

        for used in used_rows:
            if remaining <= 0:
                break
            # 중고는 등급이 존을 가른다(MINT=B / GOOD=C / NORMAL=D). 폴백도 같은 규칙을 따른다.
            loc = _location_of(
                session,
                used.location_id,
                book=book,
                grade=used.condition_grade or "NORMAL",
            )
            used.item_status = ItemStatusEnum.ALLOCATED.value
            used.updated_at = now_kst()
            session.add(used)
            draft.append(
                PickingInstructionItem(
                    instruction_id=None,  # 헤더 저장 후 채움
                    order_item_id=oi.id,
                    book_id=book.id,
                    used_item_id=used.id,
                    stock_type="USED",
                    lpn_barcode=used.lpn_barcode,
                    isbn=book.isbn,
                    title=book.title,
                    quantity=1,
                    zone=loc["zone"],
                    rack=loc["rack"],
                    shelf=loc["shelf"],
                    unit_price=oi.unit_price,
                )
            )
            remaining -= 1

        if remaining > 0:
            # 잔여분은 신품(Zone A) 벌크 피킹 - inventory 위치가 있으면 그 위치 사용
            inv = session.exec(
                select(Inventory)
                .where(Inventory.book_id == book.id)
                .where(Inventory.quantity > 0)
                .order_by(Inventory.created_at.asc())
            ).first()
            # 신품은 Zone A 고정이지만 랙/선반은 카테고리·판형이 정한다. 재고 행이 없으면
            # (아직 입고 전) 같은 알고리즘으로 배정 예정 칸을 산출한다.
            loc = _location_of(
                session, inv.location_id if inv else None, book=book, grade="NEW"
            )

            # [2026-08-10 신설] 재고 부족 라인은 있는 척하지 않는다.
            #
            # 종전에는 재고가 0이어도 평범한 피킹 항목으로 발행돼, 작업자가 빈 칸으로 가서야
            # 없다는 걸 알았다(화면상으로는 정상 지시서). 실물이 없다는 사실을 지시서에
            # 명시하고, 동시에 발주 제안을 만들어 SCM 칸반에서 채울 수 있게 한다.
            from app.domains.inventory.service import get_new_stock_qty

            available = get_new_stock_qty(session, book.id)
            out_of_stock = available < remaining

            draft.append(
                PickingInstructionItem(
                    instruction_id=None,
                    order_item_id=oi.id,
                    book_id=book.id,
                    stock_type="NEW",
                    isbn=book.isbn,
                    title=book.title,
                    quantity=remaining,
                    zone=loc["zone"],
                    rack=loc["rack"],
                    shelf=loc["shelf"],
                    unit_price=oi.unit_price,
                    status="OUT_OF_STOCK" if out_of_stock else "PENDING",
                )
            )

            if out_of_stock:
                # 발주 제안 생성 실패가 지시서 발행을 막아서는 안 된다(같은 도서의 PENDING
                # 카드가 있으면 restock 쪽이 중복 생성 대신 갱신한다).
                try:
                    from app.ai.agents.restock import generate_and_store_proposal

                    generate_and_store_proposal(
                        session,
                        book,
                        trigger_type="ORDER_SHORTAGE",
                        rejected_quantity=max(0, remaining - available),
                    )
                except Exception as e:
                    logger.warning(
                        f"[Picking] 재고부족 발주 제안 생성 실패 (지시서는 정상 발행): {e}"
                    )

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


def generate_llm_narrative(
    items: List[PickingInstructionItem], customer_name: str
) -> Dict[str, str]:
    """gpt-4o-mini로 피킹 동선 요약과 작업자 지시문을 생성한다. 실패 시 템플릿 폴백."""
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.prompts import ChatPromptTemplate
        from pydantic import BaseModel, Field as PydField

        class PickingNarrative(BaseModel):
            route_summary: str = PydField(
                description="Zone 이동 동선 중심의 2문장 이내 피킹 경로 요약 (한국어)"
            )
            worker_note: str = PydField(
                description="파손 주의/스캔 방법/적재 순서 등 작업자 지시문 2문장 이내 (한국어)"
            )

        lines = "\n".join(
            f"{it.pick_seq}. [{it.stock_type}] {it.title} x{it.quantity}권 "
            f"@ {it.zone}-{it.rack}-{it.shelf}"
            + (f" (LPN: {it.lpn_barcode})" if it.lpn_barcode else " (ISBN 스캔)")
            for it in items
        )
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2, timeout=8)
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "당신은 도서 물류센터의 피킹 작업 관제 AI입니다. "
                    "피킹 순서는 이미 규칙 엔진이 확정했으므로 변경하지 말고, "
                    "현장 작업자가 즉시 이해할 수 있는 동선 요약과 주의사항만 작성하세요.",
                ),
                ("human", "B2B 거래처: {customer}\n피킹 목록:\n{lines}"),
            ]
        )
        chain = prompt | llm.with_structured_output(PickingNarrative)
        res: PickingNarrative = chain.invoke(
            {"customer": customer_name, "lines": lines}
        )
        return {"route_summary": res.route_summary, "worker_note": res.worker_note}
    except Exception as e:
        logger.warning(f"Picking narrative LLM fallback: {e}")
        return _fallback_narrative(items)


def create_picking_instruction(
    session: Session, order: Order, use_llm: bool = True
) -> PickingInstruction:
    """주문 1건에 대한 피킹 지시서 발행 (할당 + 내러티브 + 상태 전이)"""
    items = allocate_order_items(session, order)
    if not items:
        raise ValueError("할당 가능한 재고가 없어 피킹 지시서를 발행할 수 없습니다.")

    narrative = (
        generate_llm_narrative(items, order.customer_name or "B2B 거래처")
        if use_llm
        else _fallback_narrative(items)
    )

    instruction = PickingInstruction(
        order_id=order.id,
        instruction_no=generate_instruction_no(session),
        status="PENDING",
        # 재고 부족(OUT_OF_STOCK) 라인은 스캔할 실물이 없으므로 목표 수량에서 뺀다.
        # 포함하면 지시서가 영영 완료되지 않아 **집을 수 있는 나머지 품목의 출고까지 막힌다.**
        # 부족분은 지시서에 항목으로 남아 사유가 보이고, 발주 제안으로 별도 보충된다.
        total_items=sum(it.quantity for it in items if it.status != "OUT_OF_STOCK"),
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
        extra={
            "instruction_id": str(instruction.id),
            "instruction_no": instruction.instruction_no,
        },
    )
    return instruction


def _purge_order_if_orphaned(session: Session, order_id: UUID) -> bool:
    """
    지시서가 사라진 뒤 남는 주문 껍데기를 정리한다 (order_items는 FK CASCADE).

    주문이 이 지시서 때문에만 존재했을 때에만 지운다. 출고 이력이 있거나 반품·발주가
    걸려 있으면 주문 자체가 독립된 사업 기록이므로 남긴다 — 그쪽 FK는 SET NULL이라
    지워도 에러는 안 나지만 반품 건의 출처가 조용히 끊긴다.

    현 단계 정책은 '발행 이전으로 완전 복원'이다. 주문 취소를 감사 기록으로 남기는
    요건이 생기면 이 함수 대신 취소 이력 테이블로 대체한다.
    """
    order = session.get(Order, order_id)
    if order is None or order.status == "SHIPPED":
        return False
    if session.exec(
        select(PickingInstruction).where(PickingInstruction.order_id == order_id)
    ).first():
        return False
    if session.exec(select(ReturnJob).where(ReturnJob.order_id == order_id)).first():
        return False
    if session.exec(
        select(OrderProposal).where(OrderProposal.order_id == order_id)
    ).first():
        return False

    session.delete(order)
    return True


def delete_picking_instruction(
    session: Session, instruction: PickingInstruction
) -> bool:
    """
    수락 전 지시서를 흔적 없이 제거한다 (라인 아이템은 FK CASCADE로 함께 삭제).
    주문까지 지웠으면 True를 돌려준다.

    행만 지우면 create_picking_instruction이 걸어둔 부수 효과가 남는다 —
    주문이 PICKING 상태로 붕 뜨고 중고 재고가 ALLOCATED로 영구 잠긴다.
    이미 CANCELLED된 건은 취소 시점에 되돌렸으므로 다시 만지지 않는다
    (그 사이 같은 주문에 새 지시서가 발행됐다면 그쪽 상태를 깨뜨린다).
    """
    order_id = instruction.order_id

    if instruction.status == "PENDING":
        items = session.exec(
            select(PickingInstructionItem).where(
                PickingInstructionItem.instruction_id == instruction.id
            )
        ).all()
        for it in items:
            if it.stock_type == "USED" and it.used_item_id:
                used = session.get(InventoryUsedItem, it.used_item_id)
                if used:
                    used.item_status = ItemStatusEnum.IN_STOCK.value
                    used.updated_at = now_kst()
                    session.add(used)

        order = session.get(Order, instruction.order_id)
        if order:
            order.status = "PENDING"
            order.updated_at = now_kst()
            session.add(order)

    # 발행 알림도 함께 지운다. notifications.ref_id는 FK가 아닌 문자열이라
    # 지시서를 지워도 알림 벨에 "신규 지시서 발행"이 그대로 남는다.
    stale_notes = session.exec(
        select(Notification)
        .where(Notification.ref_type == "PICKING_INSTRUCTION")
        .where(Notification.ref_id == str(instruction.id))
    ).all()
    for note in stale_notes:
        session.delete(note)

    session.delete(instruction)
    session.flush()  # 삭제를 반영해야 아래 '남은 지시서 0건' 판정이 성립한다

    purged_order = _purge_order_if_orphaned(session, order_id)
    session.commit()
    return purged_order


def serialize_instruction(
    session: Session, instruction: PickingInstruction
) -> Dict[str, Any]:
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
        "accepted_at": instruction.accepted_at.isoformat()
        if instruction.accepted_at
        else None,
        "box_id": instruction.box_id,
        "cushion_name": instruction.cushion_name,
        "cj_waybill_no": instruction.cj_waybill_no,
        "packed_at": instruction.packed_at.isoformat()
        if instruction.packed_at
        else None,
        "shipped_at": instruction.shipped_at.isoformat()
        if instruction.shipped_at
        else None,
        "created_at": instruction.created_at.isoformat()
        if instruction.created_at
        else None,
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
