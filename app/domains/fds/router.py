"""
FDS(이상거래 탐지) 관제 API. MASTER/ADMIN 전용.
탐지 로직은 전부 FdsService(룰 엔진 + Analyst Agent)에 위임한다 (2-Layer).
"""
from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.db.session import get_db
from app.core.security import RoleChecker, UserRoleEnum
from app.domains.fds.service import fds_service

router = APIRouter(
    prefix="/fds",
    tags=["FDS"],
    dependencies=[Depends(RoleChecker([UserRoleEnum.MASTER, UserRoleEnum.ADMIN]))],
)


@router.post("/scan", summary="FDS 룰 4종 전체 스캔 실행 (Analyst Agent 서술 포함)")
def run_fds_scan(session: Session = Depends(get_db)):
    """룰 엔진(결정론)으로 적발하고, 신규 건은 gpt-4o-mini Analyst가 해석/권고를 붙여 저장·알림한다."""
    return fds_service.run_scan(session)


@router.get("/reports", summary="FDS 적발 이력 목록")
def get_fds_reports(limit: int = 50, session: Session = Depends(get_db)):
    return fds_service.list_reports(session, limit=min(limit, 200))


@router.get("/summary", summary="FDS 요약 (대시보드 위젯용)")
def get_fds_summary(session: Session = Depends(get_db)):
    return fds_service.summary(session)
