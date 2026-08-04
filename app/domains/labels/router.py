"""
LPN/UBCI 열전사 라벨 출력 API.

브라우저 window.print() 경로(LpnPrintModal)와 별개로, Xprinter XP-423B
Raw TCP 직결 출력을 제공한다.

인쇄 경로는 LABEL_PRINT_MODE 설정으로 갈라진다:
- DIRECT (기본): 백엔드가 프린터 LAN IP:9100으로 즉시 전송 (로컬/온프레미스).
  LABEL_PRINTER_ENABLED=false면 ZPL 생성까지만 수행 (응답 skipped=true).
- QUEUE: 클라우드 배포용. 백엔드는 프린터 사설 IP에 닿을 수 없으므로
  label_print_jobs 테이블에 적재만 하고, 창고 PC의 프린트 브리지 에이전트
  (scripts/print_bridge_agent.py)가 폴링해 로컬 프린터로 중계한다.
"""
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.core.config import settings
from app.core.label_printer_service import (
    LabelPrinterError,
    send_zpl_to_label_printer,
)
from app.core.security import RoleChecker
from app.core.zpl_label_service import (
    build_lpn_label_zpl,
    build_ubci_label_zpl,
)
from app.db.session import get_db
from app.models.wms import LabelPrintJob, now_kst

router = APIRouter(prefix="/labels", tags=["Labels"])


class LabelPrintRequest(BaseModel):
    lpn: str = Field(min_length=1, max_length=64)
    mode: Literal["LPN", "UBCI"] = "LPN"
    # UBCI 모드 전용: 확정 등급과 점수
    condition_grade: Optional[str] = Field(default=None, max_length=16)
    ubci_score: Optional[Decimal] = None


class LabelPrintResponse(BaseModel):
    sent: bool
    skipped: bool
    queued: bool
    bytes_sent: int
    zpl: str


@router.post(
    "/print",
    response_model=LabelPrintResponse,
    dependencies=[Depends(RoleChecker(["MASTER", "ADMIN", "WORKER"]))],
)
def print_label(
    body: LabelPrintRequest,
    session: Session = Depends(get_db),
) -> LabelPrintResponse:
    """ZPL 라벨을 생성해 직접 전송(DIRECT)하거나 브리지 큐에 적재(QUEUE)한다."""
    if body.mode == "UBCI":
        if not body.condition_grade:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="UBCI 모드에는 condition_grade가 필요합니다.",
            )
        zpl = build_ubci_label_zpl(
            lpn_barcode=body.lpn,
            condition_grade=body.condition_grade,
            ubci_score=body.ubci_score,
        )
    else:
        zpl = build_lpn_label_zpl(lpn_barcode=body.lpn)

    if settings.LABEL_PRINT_MODE == "QUEUE":
        job = LabelPrintJob(lpn=body.lpn, mode=body.mode, zpl=zpl)
        session.add(job)
        return LabelPrintResponse(
            sent=False, skipped=False, queued=True, bytes_sent=0, zpl=zpl,
        )

    try:
        result = send_zpl_to_label_printer(zpl)
    except LabelPrinterError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return LabelPrintResponse(
        sent=result.sent,
        skipped=result.skipped,
        queued=False,
        bytes_sent=result.bytes_sent,
        zpl=zpl,
    )


# ------------------------------------------------------------------
# 프린트 브리지 에이전트용 API (창고 PC에서 폴링)
# ------------------------------------------------------------------

class PendingPrintJob(BaseModel):
    id: UUID
    lpn: str
    mode: str
    zpl: str
    created_at: datetime


class PrintJobAckRequest(BaseModel):
    success: bool
    error: Optional[str] = Field(default=None, max_length=500)


@router.get(
    "/jobs/pending",
    response_model=list[PendingPrintJob],
    dependencies=[Depends(RoleChecker(["MASTER", "ADMIN"]))],
)
def list_pending_print_jobs(
    limit: int = 20,
    session: Session = Depends(get_db),
) -> list[PendingPrintJob]:
    """브리지 에이전트가 가져갈 대기 인쇄 작업 목록 (오래된 순)."""
    jobs = session.exec(
        select(LabelPrintJob)
        .where(LabelPrintJob.status == "PENDING")
        .order_by(LabelPrintJob.created_at.asc())
        .limit(min(max(limit, 1), 100))
    ).all()
    return [
        PendingPrintJob(
            id=j.id, lpn=j.lpn, mode=j.mode, zpl=j.zpl, created_at=j.created_at,
        )
        for j in jobs
    ]


@router.post(
    "/jobs/{job_id}/ack",
    dependencies=[Depends(RoleChecker(["MASTER", "ADMIN"]))],
)
def ack_print_job(
    job_id: UUID,
    body: PrintJobAckRequest,
    session: Session = Depends(get_db),
) -> dict:
    """브리지 에이전트의 인쇄 결과 보고. 성공=PRINTED, 실패=FAILED(사유 보존)."""
    job = session.get(LabelPrintJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="인쇄 작업을 찾을 수 없습니다.",
        )
    if job.status != "PENDING":
        # 이미 처리된 작업 재보고는 멱등 처리 (브리지 재시작 대비)
        return {"status": job.status, "idempotent": True}

    job.status = "PRINTED" if body.success else "FAILED"
    job.error = None if body.success else (body.error or "unknown")
    job.printed_at = now_kst()
    session.add(job)
    return {"status": job.status, "idempotent": False}
