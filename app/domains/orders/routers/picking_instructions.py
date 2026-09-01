"""피킹 지시서 발급·수락·삭제와 포장 확정·되돌림·완료

orders/router.py 1,121줄 분할(2026-09-01). 본문은 원본에서 그대로 이동 - 수정 금지 원칙.
URL·인증·동작 전부 불변이며, 분할 전후 OpenAPI 경로 스냅샷 일치로 증명했다.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from app.core.security import RoleChecker
from app.db.session import get_db
from app.domains.orders.picking import (
    create_picking_instruction,
    delete_picking_instruction,
    publish_outbound_notification,
    serialize_instruction,
)
from app.domains.orders.schemas import (
    AcceptInstructionRequest,
    CompletePackingRequest,
    ConfirmPackingRequest,
)
from app.domains.orders.service import (
    _issue_waybill_no,
)
from app.models.wms import (
    Inventory,
    InventoryLog,
    InventoryUsedItem,
    ItemStatusEnum,
    Order,
    OrderStatusEnum,
    PickingInstruction,
    PickingInstructionItem,
    UserRoleEnum,
    now_kst,
)

logger = logging.getLogger(__name__)
# 라우터 전체에 인증을 건다. 엔드포인트마다 붙이면 새 경로를 추가할 때 또 빠뜨린다 -
# 실제로 재고·피킹지시서·발주제안이 무인증으로 조회되던 것을 전수 점검에서 발견했다.
# 주문·피킹·출고는 로그인 필수
# 부모(orders/router.py)가 prefix·인증 의존성을 보유한다 - 여기서는 경로만 정의한다.
router = APIRouter()

# 되돌리기 같은 재고 변경 작업은 관리자만 수행한다.
admin_only = RoleChecker([UserRoleEnum.MASTER, UserRoleEnum.ADMIN])


@router.get("/picking-instructions")
def list_picking_instructions(
    active_only: bool = Query(
        default=False, description="True면 SHIPPED/CANCELLED 제외"
    ),
    limit: int = Query(default=20, le=100),
    session: Session = Depends(get_db),
):
    """피킹 지시서 목록 (관리자 출고 페이지 / 작업자 스캐너 공용)"""
    # 번호(PICK-YYMMDD-####)로 정렬한다. 고정폭이라 문자열 내림차순이 곧
    # "날짜 우선, 같은 날짜면 일련번호 순"이 된다. created_at으로 정렬하면
    # 화면에 보이는 번호 순서와 어긋난다.
    stmt = (
        select(PickingInstruction)
        .order_by(PickingInstruction.instruction_no.desc())
        .limit(limit)
    )
    rows = session.exec(stmt).all()
    if active_only:
        rows = [r for r in rows if r.status not in ("SHIPPED", "CANCELLED")]
    return [serialize_instruction(session, r) for r in rows]


@router.get("/picking-instructions/{instruction_id}")
def get_picking_instruction(instruction_id: UUID, session: Session = Depends(get_db)):
    instruction = session.get(PickingInstruction, instruction_id)
    if not instruction:
        raise HTTPException(404, "피킹 지시서를 찾을 수 없습니다.")
    return serialize_instruction(session, instruction)


@router.post("/{order_id}/picking-instruction", status_code=status.HTTP_201_CREATED)
def issue_picking_instruction(order_id: UUID, session: Session = Depends(get_db)):
    """기존 주문에 대해 AI 피킹 지시서를 발행한다 (규칙 할당 + LLM 지시문)."""
    order = session.get(Order, order_id)
    if not order:
        raise HTTPException(404, "주문을 찾을 수 없습니다.")
    existing = session.exec(
        select(PickingInstruction).where(PickingInstruction.order_id == order.id)
    ).first()
    if existing and existing.status not in ("CANCELLED",):
        return serialize_instruction(session, existing)
    try:
        instruction = create_picking_instruction(session, order)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return serialize_instruction(session, instruction)


@router.post("/picking-instructions/{instruction_id}/accept")
def accept_picking_instruction(
    instruction_id: UUID,
    req: AcceptInstructionRequest,
    session: Session = Depends(get_db),
):
    """worker의 피킹 지시서 수락 (PENDING → ACCEPTED). 작업 배정 기록을 남긴다."""
    instruction = session.get(PickingInstruction, instruction_id)
    if not instruction:
        raise HTTPException(404, "피킹 지시서를 찾을 수 없습니다.")
    if instruction.status != "PENDING":
        raise HTTPException(
            409, f"수락 대기 상태가 아닙니다. (현재: {instruction.status})"
        )

    instruction.status = "ACCEPTED"
    instruction.accepted_by = req.worker_id
    instruction.accepted_at = now_kst()
    instruction.updated_at = now_kst()
    session.add(instruction)
    session.commit()
    return serialize_instruction(session, instruction)


@router.delete("/picking-instructions/{instruction_id}")
def delete_instruction(instruction_id: UUID, session: Session = Depends(get_db)):
    """
    수락 전 지시서 삭제 — 발행 이전 상태로 되돌린다.
    중고 재고 할당 해제 + 주문 상태 복구 + 발행 알림 제거까지 한 트랜잭션에서 처리하고,
    이 지시서 때문에만 존재하던 주문이면 주문 껍데기도 함께 정리한다.
    """
    instruction = session.get(PickingInstruction, instruction_id)
    if not instruction:
        raise HTTPException(404, "피킹 지시서를 찾을 수 없습니다.")
    if instruction.status not in ("PENDING", "CANCELLED"):
        raise HTTPException(
            409, f"수락 전 지시서만 삭제할 수 있습니다. (현재: {instruction.status})"
        )

    instruction_no = instruction.instruction_no
    purged_order = delete_picking_instruction(session, instruction)
    return {
        "deleted": True,
        "instruction_no": instruction_no,
        "order_purged": purged_order,
    }


@router.post("/picking-instructions/{instruction_id}/confirm-packing")
def confirm_packing(
    instruction_id: UUID, req: ConfirmPackingRequest, session: Session = Depends(get_db)
):
    """
    [admin] 패킹 박스 확정 → CJ 송장 발급 → DB 재고 실차감 (신품 inventory 차감 / 중고 SHIPPED 전환)
    상태는 PACKED로 전이되며, worker가 송장/적재 가이드를 확인하고 포장 완료(complete-packing)해야 최종 SHIPPED가 된다.
    """
    instruction = session.get(PickingInstruction, instruction_id)
    if not instruction:
        raise HTTPException(404, "피킹 지시서를 찾을 수 없습니다.")
    if instruction.status == "SHIPPED":
        raise HTTPException(
            409, f"이미 출고 완료된 지시서입니다. (송장: {instruction.cj_waybill_no})"
        )
    if instruction.status == "PACKED":
        raise HTTPException(
            409,
            f"이미 송장이 발급되어 worker 포장 대기 중입니다. (송장: {instruction.cj_waybill_no})",
        )
    if instruction.status not in ("PICKED",) and not req.force:
        raise HTTPException(
            409,
            f"전 품목 피킹 완료 전입니다 ({instruction.picked_items}/{instruction.total_items}). "
            "강제 확정은 force=true로 요청하세요.",
        )

    items = session.exec(
        select(PickingInstructionItem).where(
            PickingInstructionItem.instruction_id == instruction.id
        )
    ).all()

    for it in items:
        if it.stock_type == "USED" and it.used_item_id:
            used = session.get(InventoryUsedItem, it.used_item_id)
            # 재고 행이 없으면 등급을 지어내지 않고 확정을 중단한다. 송장 발급·재고 차감이
            # 걸린 확정 행위라, 원장에 근거 없는 등급을 남기며 진행하면 안 된다.
            if not used:
                raise HTTPException(
                    409,
                    f"중고 재고를 찾을 수 없습니다 (LPN: {it.lpn_barcode}). "
                    "재고 원장을 확인한 뒤 다시 확정하세요.",
                )
            used.item_status = ItemStatusEnum.SHIPPED.value
            used.updated_at = now_kst()
            session.add(used)
            session.add(
                InventoryLog(
                    transaction_type="OUTBOUND",
                    book_id=it.book_id,
                    condition_grade=used.condition_grade,
                    quantity_change=-1,
                    target_lpn=it.lpn_barcode,
                    picked_location=f"{it.zone}-{it.rack}-{it.shelf}",
                )
            )
        else:
            remaining = it.quantity
            inv_rows = session.exec(
                select(Inventory)
                .where(Inventory.book_id == it.book_id)
                .where(Inventory.quantity > 0)
                .order_by(Inventory.created_at.asc())
            ).all()
            for inv in inv_rows:
                if remaining <= 0:
                    break
                deduct = min(inv.quantity, remaining)
                inv.quantity -= deduct
                inv.updated_at = now_kst()
                session.add(inv)
                remaining -= deduct
            if remaining > 0:
                logger.warning(
                    f"[출고] 재고 부족분 {remaining}권이 차감되지 않았습니다 "
                    f"(book_id={it.book_id}, 지시수량={it.quantity})."
                )
            session.add(
                InventoryLog(
                    transaction_type="OUTBOUND",
                    book_id=it.book_id,
                    # 신품은 UBCI 검수를 타지 않아 등급이 없다. 입고 로그와 같이 "NEW"로 구분값을 쓴다.
                    condition_grade="NEW",
                    quantity_change=-it.quantity,
                    picked_location=f"{it.zone}-{it.rack}-{it.shelf}",
                )
            )

    waybill = _issue_waybill_no(session)
    instruction.status = "PACKED"
    instruction.box_id = req.box_id
    instruction.cushion_name = req.cushion_name
    instruction.cj_waybill_no = waybill
    instruction.packed_at = now_kst()
    instruction.updated_at = now_kst()
    session.add(instruction)
    session.commit()

    # worker 실시간 알림: 송장 발급 - 포장 작업 요청
    publish_outbound_notification(
        event_type="WAYBILL_ISSUED",
        category="송장 발급",
        title=f"CJ 송장 발급 [{waybill}]",
        description=f"{instruction.instruction_no} 패킹 확정 (박스: {req.box_id}) - 스캐너 화면의 적재 가이드에 따라 포장 후 완료 처리하세요.",
        extra={
            "instruction_id": str(instruction.id),
            "instruction_no": instruction.instruction_no,
        },
    )

    return {
        "status": "PACKED",
        "instruction_no": instruction.instruction_no,
        "order_id": str(instruction.order_id),
        "box_id": req.box_id,
        "cushion_name": req.cushion_name,
        "courier": "CJ대한통운",
        "cj_waybill_no": waybill,
        "packed_at": instruction.packed_at.isoformat(),
        "message": f"패킹 확정 및 CJ 송장 [{waybill}] 발급, DB 재고 차감 완료 - worker 포장 대기",
    }


@router.post("/picking-instructions/{instruction_id}/revert-packing")
def revert_packing(
    instruction_id: UUID,
    session: Session = Depends(get_db),
    current_admin=Depends(admin_only),
):
    """
    [admin] 패킹 확정을 되돌려 피킹 완료(PICKED) 상태로 복원한다.

    confirm_packing이 한 일을 정확히 역으로 되돌린다 - 중고 SHIPPED 해제,
    신품 수량 복원, 송장·박스 정보 제거, 상태 PICKED 전이.
    재고를 손대는 작업이라 SHIPPED(최종 출고) 건은 대상에서 제외한다.

    InventoryLog는 지우지 않는다. 차감 기록과 복원 기록이 모두 남아야
    "왜 수량이 이렇게 됐는지"를 나중에 되짚을 수 있다.
    """
    instruction = session.get(PickingInstruction, instruction_id)
    if not instruction:
        raise HTTPException(status_code=404, detail="지시서를 찾을 수 없습니다.")
    if instruction.status == "SHIPPED":
        raise HTTPException(
            status_code=409, detail="이미 출고 완료된 지시서는 되돌릴 수 없습니다."
        )
    if instruction.status != "PACKED":
        raise HTTPException(
            status_code=409,
            detail=f"패킹 확정(PACKED) 상태만 되돌릴 수 있습니다. 현재: {instruction.status}",
        )

    items = session.exec(
        select(PickingInstructionItem).where(
            PickingInstructionItem.instruction_id == instruction_id
        )
    ).all()

    restored_used, restored_new = 0, 0
    for it in items:
        if it.used_item_id:
            used = session.get(InventoryUsedItem, it.used_item_id)
            if used and used.item_status == ItemStatusEnum.SHIPPED.value:
                used.item_status = ItemStatusEnum.IN_STOCK.value
                used.updated_at = now_kst()
                session.add(used)
                restored_used += 1
                session.add(
                    InventoryLog(
                        transaction_type="INBOUND",
                        book_id=it.book_id,
                        condition_grade=used.condition_grade or "GOOD",
                        quantity_change=1,
                        target_lpn=it.lpn_barcode,
                        picked_location=f"{it.zone}-{it.rack}-{it.shelf}",
                    )
                )
        else:
            # 신품은 차감했던 수량을 되돌린다. 어느 로케이션에서 뺐는지는
            # 기록이 남지 않으므로 해당 도서의 첫 재고 행에 합산한다.
            inv = session.exec(
                select(Inventory)
                .where(Inventory.book_id == it.book_id)
                .order_by(Inventory.created_at.asc())
            ).first()
            if inv:
                inv.quantity += int(it.quantity or 0)
                inv.updated_at = now_kst()
                session.add(inv)
                restored_new += int(it.quantity or 0)
                session.add(
                    InventoryLog(
                        transaction_type="INBOUND",
                        book_id=it.book_id,
                        condition_grade="MINT",
                        quantity_change=int(it.quantity or 0),
                        picked_location=f"{it.zone}-{it.rack}-{it.shelf}",
                    )
                )

    prev_waybill = instruction.cj_waybill_no
    instruction.status = "PICKED"
    instruction.cj_waybill_no = None
    instruction.box_id = None
    instruction.cushion_name = None
    instruction.packed_at = None
    instruction.updated_at = now_kst()
    session.add(instruction)
    session.commit()

    return {
        "status": "PICKED",
        "instruction_no": instruction.instruction_no,
        "reverted_waybill": prev_waybill,
        "restored_used_items": restored_used,
        "restored_new_qty": restored_new,
        "message": (
            f"{instruction.instruction_no} 패킹 확정을 되돌렸습니다 "
            f"(중고 {restored_used}건 재입고 · 신품 {restored_new}권 복원, 송장 {prev_waybill} 회수)."
        ),
    }


@router.post("/picking-instructions/{instruction_id}/complete-packing")
def complete_packing(
    instruction_id: UUID,
    req: CompletePackingRequest,
    session: Session = Depends(get_db),
):
    """
    [worker] 발급된 송장/적재 가이드 확인 후 실물 포장 완료 → 최종 출고(SHIPPED) 전이
    """
    instruction = session.get(PickingInstruction, instruction_id)
    if not instruction:
        raise HTTPException(404, "피킹 지시서를 찾을 수 없습니다.")
    if instruction.status == "SHIPPED":
        raise HTTPException(409, "이미 출고 완료된 지시서입니다.")
    if instruction.status != "PACKED":
        raise HTTPException(
            409,
            f"송장 발급(패킹 확정) 전입니다. (현재: {instruction.status}) - 관리자 출고 화면에서 패킹 확정을 먼저 진행하세요.",
        )

    instruction.status = "SHIPPED"
    instruction.shipped_at = now_kst()
    instruction.updated_at = now_kst()
    session.add(instruction)

    order = session.get(Order, instruction.order_id)
    if order:
        order.status = OrderStatusEnum.SHIPPED.value
        order.updated_at = now_kst()
        session.add(order)
    session.commit()

    publish_outbound_notification(
        event_type="OUTBOUND_SHIPPED",
        category="출고 완료",
        title=f"최종 출고 완료 [{instruction.instruction_no}]",
        description=f"작업자 {req.worker_id} 포장 완료 - 송장 {instruction.cj_waybill_no} CJ대한통운 인계 대기.",
        extra={
            "instruction_id": str(instruction.id),
            "instruction_no": instruction.instruction_no,
        },
    )

    return {
        "status": "SHIPPED",
        "instruction_no": instruction.instruction_no,
        "cj_waybill_no": instruction.cj_waybill_no,
        "box_id": instruction.box_id,
        "shipped_at": instruction.shipped_at.isoformat(),
        "message": f"[{instruction.instruction_no}] 포장 완료 및 최종 출고 확정 (송장: {instruction.cj_waybill_no})",
    }
