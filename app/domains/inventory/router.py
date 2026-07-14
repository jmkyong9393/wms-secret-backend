from fastapi import APIRouter
from typing import List, Dict, Any
from datetime import datetime

# Inventory 도메인 라우터: 새 상품 및 중고/반품 도서들의 통합 재고 관리를 담당합니다.
router = APIRouter(prefix="/inventory", tags=["Inventory"])


@router.get("/")
async def get_inventory() -> List[Dict[str, Any]]:
    """
    프론트엔드 DataGrid에 출력하기 위한 전체 통합 재고 목록을 조회합니다.
    기존의 복잡했던 DB 필드명을 프론트엔드 인터페이스에 맞춰 가공(Mapping)하여 반환합니다.
    (예: inventory_id -> id, location_id -> zone 등)
    """
    return [
        {
            "id": "uuid-inv-1",
            "book": {
                "title": "사피엔스",
                "isbn": "9788912345678"
            },
            "grade": "MINT",
            "zone": "A-1-3",
            "quantity": 100,
            "date": datetime.utcnow().isoformat()
        },
        {
            "id": "uuid-inv-2",
            "book": {
                "title": "이기적 유전자",
                "isbn": "9788912345679"
            },
            "grade": "GOOD",
            "zone": "B-2-1",
            "quantity": 15,
            "date": datetime.utcnow().isoformat()
        }
    ]
