from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db.session import get_db
from app.core.constants import format_worker_label
from app.core.security import RoleChecker
from app.models.wms import UserRoleEnum
from app.domains.po.service import po_service

router = APIRouter(prefix="/po", tags=["Auto PO (SCM 칸반)"])

# 발주 결재는 관리자 전용 (HITL 오버라이드와 동일 권한 체계)
admin_only = RoleChecker([UserRoleEnum.MASTER, UserRoleEnum.ADMIN])


class ProposalDecisionRequest(BaseModel):
    proposal_ids: List[str] = Field(
        ..., description="결재할 order_proposals id 목록 (단건도 배열로)"
    )


def _inspector_label(current_admin) -> str:
    employee_id = str(getattr(current_admin, "employee_id", "") or "").strip()
    name = str(getattr(current_admin, "name", "") or "").strip()
    if not (employee_id or name):
        return "PO 관리자"
    return format_worker_label(employee_id, name)


@router.get("/proposals")
def list_proposals(
    status: Optional[str] = Query(
        default=None, description="PENDING | APPROVED | DISMISSED (미지정 시 전체)"
    ),
    db: Session = Depends(get_db),
    current_admin=Depends(admin_only),
) -> List[Dict[str, Any]]:
    """
    SCM 칸반보드 카드 목록. 제안 생성은 반려 이벤트/저재고 스캔 시점(write-time)에 이미
    완료되어 있으므로 이 GET은 DB 조회만 한다 - 과거 /po/suggested처럼 페이지 로드마다
    LLM을 호출하지 않는다.
    """
    return po_service.list_proposals(db, status)


@router.post("/proposals/approve")
def approve_proposals(
    req: ProposalDecisionRequest,
    db: Session = Depends(get_db),
    current_admin=Depends(admin_only),
):
    """
    제안 승인 = 집행. Order(AUTO_PO) 생성 후 신품 Fast-Track 입고(Zone A 묶음 재고 +
    virtual_stock 가산, LPN 미발급)까지 완료한다. LLM 제안은 이 관리자 승인 게이트를
    통과해야만 실제 발주가 된다.
    """
    return po_service.approve_proposals(
        db,
        req.proposal_ids,
        decided_by=_inspector_label(current_admin),
        worker_employee_id=str(getattr(current_admin, "employee_id", "") or "").strip(),
    )


@router.post("/proposals/dismiss")
def dismiss_proposals(
    req: ProposalDecisionRequest,
    db: Session = Depends(get_db),
    current_admin=Depends(admin_only),
):
    return po_service.dismiss_proposals(
        db, req.proposal_ids, decided_by=_inspector_label(current_admin)
    )


@router.post("/proposals/delete")
def delete_proposals(
    req: ProposalDecisionRequest,
    db: Session = Depends(get_db),
    current_admin=Depends(admin_only),
):
    """
    결재 완료(APPROVED/DISMISSED) 카드를 보드에서 삭제한다. PENDING 카드는 거부한다.

    DELETE가 아니라 POST인 것은 본문에 id 배열을 담아 여러 건을 한 번에 정리하기 위함이며,
    approve/dismiss와 요청 형태를 맞춘다.
    """
    return po_service.delete_proposals(db, req.proposal_ids)


@router.post("/proposals/scan")
def scan_safety_stock(
    db: Session = Depends(get_db),
    current_admin=Depends(admin_only),
):
    """
    저재고 수동 스캔 트리거. 가용 재고(신품+중고)가 안전선(system_settings의
    safety_stock_threshold, GET/PUT /api/v1/admin/settings로 조회/변경) 미만인 도서에
    대해 Restock 판정 그래프를 실행해 PENDING 제안 카드를 생성한다
    (1회 최대 POService.SCAN_LIMIT건).
    """
    return po_service.scan_safety_stock(db)
