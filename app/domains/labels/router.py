"""
LPN/UBCI 열전사 라벨 출력 API.

브라우저 window.print() 경로(LpnPrintModal)와 별개로, Xprinter XP-423B
Raw TCP 직결 출력을 제공한다. LABEL_PRINTER_ENABLED=false(기본)면
ZPL 생성까지만 수행하고 전송은 건너뛴다 (응답의 skipped=true).
"""
from decimal import Decimal
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.label_printer_service import (
    LabelPrinterError,
    send_zpl_to_label_printer,
)
from app.core.security import RoleChecker
from app.core.zpl_label_service import (
    build_lpn_label_zpl,
    build_ubci_label_zpl,
)

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
    bytes_sent: int
    zpl: str


@router.post(
    "/print",
    response_model=LabelPrintResponse,
    dependencies=[Depends(RoleChecker(["MASTER", "ADMIN", "WORKER"]))],
)
def print_label(body: LabelPrintRequest) -> LabelPrintResponse:
    """ZPL 라벨을 생성해 네트워크 라벨 프린터로 전송한다."""
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
        bytes_sent=result.bytes_sent,
        zpl=zpl,
    )
