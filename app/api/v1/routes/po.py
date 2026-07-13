from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Dict, Any

# Auto PO 도메인 라우터: AI 예측 기반 가상 재고(Virtual Stock) 고갈 시 자동 발주 기능을 제공합니다.
router = APIRouter(prefix="/po", tags=["Auto PO"])

class ApproveRequest(BaseModel):
    """
    프론트엔드에서 발주 승인 시 전송하는 요청 바디의 Pydantic 모델입니다.
    """
    book_ids: List[str]

@router.get("/suggested")
async def get_suggested_po() -> List[Dict[str, Any]]:
    """
    현재 DB의 virtual_stock(가상 재고) 수량을 기반으로 발주가 필요한(추천된) 도서 목록을 반환합니다.
    """
    return [
        {
            "book_id": "uuid-book-1",
            "title": "이기적 유전자",
            "current_stock": 12,
            "suggested_qty": 50,
            "urgency": "HIGH"
        },
        {
            "book_id": "uuid-book-2",
            "title": "클린 코드",
            "current_stock": 5,
            "suggested_qty": 30,
            "urgency": "CRITICAL"
        }
    ]

@router.post("/approve")
async def approve_po(req: ApproveRequest):
    """
    관리자가 추천 발주 목록 중 일부를 체크하여 '승인' 버튼을 눌렀을 때 호출됩니다.
    [MVP 단계] 현재는 실제 DB 테이블(orders 등)에 Insert하지 않고,
    성공했다는 메시지("success")만 반환하도록 협의되었습니다.
    """
    return {
        "message": "success",
        "approved_count": len(req.book_ids)
    }
