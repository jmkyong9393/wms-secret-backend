"""대시보드 라우터 - 권한과 배선 전용.

SQL 집계 로직은 service.py, 주간 인사이트는 weekly_insight_service가 담당한다
(2026-09-01 계층 정리).
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.core.security import RoleChecker, UserRoleEnum
from app.db.session import get_db
from app.domains.dashboard import service
from app.domains.dashboard.weekly_insight_service import (
    build_weekly_insight,
    iso_week_bounds,
    serialize_weekly_insight,
)
from app.models.wms import WeeklyInsight, now_kst

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"],
    dependencies=[Depends(RoleChecker([UserRoleEnum.MASTER, UserRoleEnum.ADMIN]))],
)


@router.get("/kpi")
def get_kpi(session: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    오늘의 실시간 핵심 성과 지표(KPI)를 DB SQL 집계 쿼리로 반환합니다.

    자동 승인율/반려율이 프론트에 91.7%/4.8%로 하드코딩되어 있던 것을
    return_jobs 실집계로 교체 - approval_rate/rejection_rate/hitl_rate 필드 신설.
    """
    return service.get_kpi(session)


@router.get("/inspection-breakdown")
def get_inspection_breakdown(session: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    검수 경로를 **책 1권 단위 최종 상태**로 집계해 합이 100%가 되게 반환한다.
    분모는 **통합 재고**다 - 중고 LPN 1행 = 1권, 신품은 묶음 재고 수량(권)을 더한다.

    분류 기준·우선순위·소급 한계는 service.get_inspection_breakdown 참조.
    """
    return service.get_inspection_breakdown(session)


@router.get("/charts")
def get_dashboard_charts(session: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    14일간 일별 입출고 물량, 등급 분포, 카테고리 분포 SQL 실집계 데이터 반환.
    """
    return service.get_dashboard_charts(session)


@router.get("/logs")
def get_dashboard_logs(
    limit: int = Query(30, ge=1, le=100),
    since: Optional[datetime] = Query(
        None,
        description="이 시각 이후 생성된 건만 반환. 화면의 로그 비우기가 기준 시각을 넘겨 쓴다.",
    ),
    session: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """
    최근 AI 비전 검수 트랜잭션 로그 (return_jobs 원장, 최신순).

    `since`는 화면 표시를 자르기 위한 값일 뿐 원장을 지우지 않는다. 검수 이력은
    매입가 산정 근거이자 감사 대상이므로 조회 API가 삭제 수단을 제공하지 않는다.
    """
    return service.get_dashboard_logs(session, limit, since)


@router.get("/weekly-insights")
def get_weekly_insights(session: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    주간 인사이트 스냅샷 조회 (weekly_insights 테이블).

    집계 로직은 weekly_insight_service로 분리했고, 정규 생성 주체는
    Celery Beat(매일 00:05 KST)이다. 여기서는 조회만 하되, 크론이 아직 안 돌았거나 실패한
    경우를 대비해 같은 함수로 폴백 생성한다(자기치유). 집계 창은 ISO 주 경계로 고정되므로
    누가 언제 방문하든 같은 주차는 같은 값을 낸다.
    """
    report_week, _, _ = iso_week_bounds(now_kst())
    existing = session.exec(
        select(WeeklyInsight).where(WeeklyInsight.report_week == report_week)
    ).first()
    if existing:
        return serialize_weekly_insight(existing, cached=True)

    insight, created = build_weekly_insight(session)
    return serialize_weekly_insight(insight, cached=not created)


@router.get("/weekly-insights/history")
def get_weekly_insights_history(
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    지난 주간 인사이트 이력.

    weekly_insights는 지연 물질화라 실제로 방문된 주만 행이 쌓인다 - ISO 주 1건뿐이라
    1년에 52행 수준이라 용량 문제는 아니다. 다만 기존에는 이번 주 1건만 조회하는
    엔드포인트뿐이라 과거 주를 볼 방법이 없었다. HITL 대기열처럼 전체를 한 번에 fetch하는
    패턴을 반복하지 않도록, 여기서는 처음부터 limit/offset 배치 페이지네이션을 강제한다
    (프론트는 "더보기" 버튼으로 다음 배치를 이어붙인다).
    """
    from sqlmodel import func

    total = session.exec(select(func.count(WeeklyInsight.id))).one()
    rows = session.exec(
        select(WeeklyInsight)
        .order_by(WeeklyInsight.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    return {
        "items": [serialize_weekly_insight(w, cached=True) for w in rows],
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
    }


@router.get("/ai-quality")
def get_ai_quality_stats(session: Session = Depends(get_db)) -> Dict[str, int]:
    """
    AI 비전 모델 상태 등급 DB SQL 집계 통계 (ubci_score 기반 - final_grade 컬럼 없음)
    """
    return service.get_ai_quality_stats(session)
