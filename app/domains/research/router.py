"""연구·분석 라우터 - 권한과 배선 전용. 집계 로직은 service.py (2026-09-01 계층 정리)."""

from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.security import RoleChecker
from app.db.session import get_db
from app.domains.research import service
from app.models.wms import UserRoleEnum

router = APIRouter(prefix="/research", tags=["Research & Analytics"])

# 데이터셋 export는 다른 관리자 도메인과 동일하게 MASTER/ADMIN 공통 권한.
admin_only = RoleChecker([UserRoleEnum.MASTER, UserRoleEnum.ADMIN])
# FDS 리포트는 관리자별 검수 속도(blind approval 의심)를 분석하는 자기감시성 리포트라
# ADMIN이 자신의 이상 승인 패턴을 스스로 조회할 수 있게 되는 걸 막기 위해 MASTER 전용 유지.
master_only = RoleChecker([UserRoleEnum.MASTER])


@router.get("/export-dataset")
def export_mlops_dataset(
    session: Session = Depends(get_db), current_admin=Depends(admin_only)
):
    """
    MLOps용 BBox 좌표 데이터셋 추출기 (SCI 논문용)
    HITL 대시보드에서 관리자가 검증 완료(Approved)한 좌표(defectCoordinates)를
    AI 학습(YOLO/COCO)을 위해 정제하여 JSON으로 반환합니다.
    """
    return service.export_mlops_dataset(session)


@router.get("/fds-report")
def generate_fds_report(
    session: Session = Depends(get_db), current_admin=Depends(master_only)
):
    """
    작업자 신뢰성 및 모럴 해저드 방어를 위한 FDS 리포트 (관리자별 결재 행태 분석).

    종전에는 **전 기간 누적 평균 < 1초**로 판정했다. FDS 룰 엔진
    R1과 같은 결함이 있었다: ① 과거 신중한 결재가 현재의 블라인드 결재를 영구히 희석하고,
    ② 평균은 이상치 한 건에 무너진다(200초 결재 1건이 0.5초 결재 12건을 덮는다).
    판정 기준을 `app/domains/fds/service.py`의 R1과 **동일한 정의**로 통일한다 —
    같은 현상을 두 화면이 다르게 판정하면 어느 쪽도 신뢰할 수 없기 때문이다.
    """
    return service.generate_fds_report(session)


@router.get(
    "/hitl-recheck-list", summary="HITL 결재를 거친 도서 LPN 목록 (재검수 전수조사용)"
)
def list_hitl_reviewed_items(
    only_recalled: bool = False,
    session: Session = Depends(get_db),
    current_admin=Depends(admin_only),
) -> Dict[str, Any]:
    """
    관리자가 결재한 검수 건을 **LPN 단위**로 집계한다.

    같은 도서를 여러 번 재검수하면 감사 로그가 여러 줄 쌓인다. 건별로 세면 한 권이
    여러 번 계산되므로 **LPN마다 마지막 조치만** 남기고, 조치 횟수는 별도 열로 센다.

    only_recalled=true면 관리자가 재고 화면에서 직접 되불러온 건(ADMIN_RECALL)만 추린다.
    """
    return service.list_hitl_reviewed_items(session, only_recalled)
