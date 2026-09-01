"""라벨 인쇄 업무 규칙 - 모드 분기·브리지 큐 적재·인쇄 결과의 멱등 전이.

ZPL 생성(core/zpl_label_service)과 프린터 전송(core/label_printer_service)은
기존 core 계층이 담당한다. 여기는 그 둘을 잇는 판단만 둔다.
본문은 router에서 이동(2026-09-01 계층 정리) — 로직 불변, 요청 모델 언패킹과
dict 반환(응답 직렬화는 라우터 response_model 몫)만 조정했다.
"""

from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.core.config import settings
from app.core.label_printer_service import (
    LabelPrinterError,
    send_zpl_to_label_printer,
)
from app.core.zpl_label_service import (
    build_lpn_label_zpl,
    build_ubci_label_zpl,
)
from app.models.wms import LabelPrintJob, now_kst


def print_label(
    db: Session,
    *,
    lpn: str,
    mode: str,
    book_title: Optional[str],
    isbn: Optional[str],
    worker_id: Optional[str],
    condition_grade: Optional[str],
    ubci_score: Optional[Decimal],
) -> dict:
    """ZPL을 생성해 직접 전송(DIRECT)하거나 브리지 큐에 적재(QUEUE)한다."""
    if mode == "UBCI":
        if not condition_grade:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="UBCI 모드에는 condition_grade가 필요합니다.",
            )
        zpl = build_ubci_label_zpl(
            lpn_barcode=lpn,
            condition_grade=condition_grade,
            ubci_score=ubci_score,
        )
    else:
        zpl = build_lpn_label_zpl(
            lpn_barcode=lpn,
            book_title=book_title or "",
            isbn=isbn or "",
            worker_id=worker_id or "",
        )

    if settings.LABEL_PRINT_MODE == "QUEUE":
        job = LabelPrintJob(lpn=lpn, mode=mode, zpl=zpl)
        db.add(job)
        return {
            "sent": False,
            "skipped": False,
            "queued": True,
            "bytes_sent": 0,
            "zpl": zpl,
        }

    try:
        result = send_zpl_to_label_printer(zpl)
    except LabelPrinterError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return {
        "sent": result.sent,
        "skipped": result.skipped,
        "queued": False,
        "bytes_sent": result.bytes_sent,
        "zpl": zpl,
    }


def list_pending_jobs(db: Session, limit: int) -> list[LabelPrintJob]:
    """브리지 에이전트가 가져갈 대기 인쇄 작업 (오래된 순)."""
    return list(
        db.exec(
            select(LabelPrintJob)
            .where(LabelPrintJob.status == "PENDING")
            .order_by(LabelPrintJob.created_at.asc())
            .limit(min(max(limit, 1), 100))
        ).all()
    )


def ack_job(db: Session, job_id: UUID, *, success: bool, error: Optional[str]) -> dict:
    """인쇄 결과 보고. 성공=PRINTED, 실패=FAILED(사유 보존). 재보고는 멱등."""
    job = db.get(LabelPrintJob, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="인쇄 작업을 찾을 수 없습니다.",
        )
    if job.status != "PENDING":
        # 이미 처리된 작업 재보고는 멱등 처리 (브리지 재시작 대비)
        return {"status": job.status, "idempotent": True}

    job.status = "PRINTED" if success else "FAILED"
    job.error = None if success else (error or "unknown")
    job.printed_at = now_kst()
    db.add(job)
    return {"status": job.status, "idempotent": False}
