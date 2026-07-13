from fastapi import APIRouter
from typing import List, Dict, Any
from datetime import datetime

# Inbound 도메인 라우터: 협력사(B2B) 또는 일반 사용자의 입고 요청 및 처리 이력을 담당합니다.
router = APIRouter(prefix="/inbound", tags=["Inbound"])

@router.get("/history")
async def get_inbound_history() -> List[Dict[str, Any]]:
    """
    최근 처리된 입고 작업(NEW_STOCK, CUSTOMER_RETURN 등)의 이력을 반환합니다.
    (실제 구현 시 inbound_jobs 테이블을 조회하여 페이징 처리 예정)
    """
    return [
        {
            "inbound_id": "uuid-inbound-1",
            "inbound_type": "NEW_STOCK",
            "supplier_name": "교보문고",
            "status": "COMPLETED",
            "total_quantity": 250,
            "date": datetime.utcnow().isoformat()
        },
        {
            "inbound_id": "uuid-inbound-2",
            "inbound_type": "CUSTOMER_RETURN",
            "supplier_name": "B2B고객사A",
            "status": "CHECKING",
            "total_quantity": 12,
            "date": datetime.utcnow().isoformat()
        }
    ]
