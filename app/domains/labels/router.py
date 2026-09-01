"""
LPN/UBCI 열전사 라벨 출력 API — HTTP 스키마·권한과 배선 전용.

브라우저 window.print() 경로(LpnPrintModal)와 별개로, Xprinter XP-423B
Raw TCP 직결 출력을 제공한다. 모드 분기·큐 적재·멱등 전이 등 업무 규칙은
service.py, ZPL 생성·전송은 core 계층이 담당한다 (2026-09-01 계층 정리).

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

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.core.security import RoleChecker
from app.db.session import get_db
from app.domains.labels import service

router = APIRouter(prefix="/labels", tags=["Labels"])


class LabelPrintRequest(BaseModel):
    lpn: str = Field(min_length=1, max_length=64)
    mode: Literal["LPN", "UBCI"] = "LPN"
    # LPN 모드 전용: 선부착 시점에 이미 확정된 도서 식별 정보
    book_title: Optional[str] = Field(default=None, max_length=200)
    isbn: Optional[str] = Field(default=None, max_length=20)
    worker_id: Optional[str] = Field(default=None, max_length=64)
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
    result = service.print_label(
        session,
        lpn=body.lpn,
        mode=body.mode,
        book_title=body.book_title,
        isbn=body.isbn,
        worker_id=body.worker_id,
        condition_grade=body.condition_grade,
        ubci_score=body.ubci_score,
    )
    return LabelPrintResponse(**result)


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
    jobs = service.list_pending_jobs(session, limit)
    return [
        PendingPrintJob(
            id=j.id,
            lpn=j.lpn,
            mode=j.mode,
            zpl=j.zpl,
            created_at=j.created_at,
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
    return service.ack_job(session, job_id, success=body.success, error=body.error)
