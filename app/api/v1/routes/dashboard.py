from fastapi import APIRouter
from typing import Dict, List, Any
from datetime import datetime

# Dashboard 도메인 라우터: 관리자 대시보드 화면에 필요한 통계 및 로깅 데이터를 제공합니다.
router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/kpi")
async def get_kpi() -> Dict[str, int]:
    """
    오늘의 핵심 성과 지표(KPI)를 조회합니다.
    [MVP 단계] 현재는 프론트엔드 연동 테스트를 위해 하드코딩된 더미 데이터를 반환하며,
    추후 DB(inventory_logs 등) 집계 쿼리로 대체됩니다.
    """
    return {
        "today_inbound": 125,
        "today_outbound": 432,
        "today_inspection": 89,
        "pending_issues": 3
    }

@router.get("/logs")
async def get_dashboard_logs() -> List[Dict[str, Any]]:
    """
    최근 발생한 재고 입/출고 및 변동 트랜잭션 로그를 조회합니다.
    대시보드의 'Recent Activity' 섹션에 표출됩니다.
    """
    return [
        {
            "id": "uuid-1",
            "transaction_type": "INBOUND",
            "book_title": "총균쇠",
            "condition_grade": "MINT",
            "quantity_change": 50,
            "date": datetime.utcnow().isoformat()
        },
        {
            "id": "uuid-2",
            "transaction_type": "OUTBOUND",
            "book_title": "사피엔스",
            "condition_grade": "MINT",
            "quantity_change": -2,
            "date": datetime.utcnow().isoformat()
        }
    ]

@router.get("/ai-quality")
async def get_ai_quality_stats() -> Dict[str, int]:
    """
    AI 비전 모델이 검수한 도서들의 상태 등급(MINT, GOOD 등)별 통계를 조회합니다.
    """
    return {
        "MINT": 45,
        "GOOD": 30,
        "NORMAL": 20,
        "REJECT": 5
    }
