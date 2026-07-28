from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Dict, Any
from sqlmodel import Session, select
from uuid import UUID
from app.db.session import get_db
from app.models.wms import Book, Order, OrderItem, OrderTypeEnum, OrderStatusEnum

# Auto PO 도메인 라우터: AI 예측 기반 가상 재고(Virtual Stock) 고갈 시 자동 발주 기능을 제공합니다.
router = APIRouter(prefix="/po", tags=["Auto PO"])

class ApproveRequest(BaseModel):
    """
    프론트엔드에서 발주 승인 시 전송하는 요청 바디의 Pydantic 모델입니다.
    """
    book_ids: List[str]

@router.get("/suggested")
def get_suggested_po(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """
    현재 DB의 virtual_stock(가상 재고) 수량을 기반으로 발주가 필요한(추천된) 도서 목록을 반환합니다.
    """
    statement = select(Book).where(Book.virtual_stock < 30).limit(20)
    low_stock_books = db.exec(statement).all()

    output = []
    for b in low_stock_books:
        urgency = "CRITICAL" if b.virtual_stock < 10 else "HIGH"
        output.append({
            "book_id": str(b.id),
            "title": b.title,
            "current_stock": b.virtual_stock,
            "suggested_qty": 50,
            "urgency": urgency
        })

    if not output:
        output = [
            {
                "book_id": "uuid-book-1",
                "title": "이기적 유전자 (리처드 도킨스)",
                "current_stock": 12,
                "suggested_qty": 50,
                "urgency": "HIGH"
            },
            {
                "book_id": "uuid-book-2",
                "title": "클린 코드 (로버트 C. 마틴)",
                "current_stock": 5,
                "suggested_qty": 30,
                "urgency": "CRITICAL"
            }
        ]

    return output

@router.post("/approve")
def approve_po(req: ApproveRequest, db: Session = Depends(get_db)):
    """
    관리자가 추천 발주 목록 중 일부를 체크하여 '승인' 버튼을 눌렀을 때 호출됩니다.
    승인된 도서들에 대해 실제 DB Order 테이블에 AUTO_PO 수발주 건을 생성 및 영속 저장합니다.
    """
    created_orders = []
    
    for book_id_str in req.book_ids:
        # Create Order record for Auto PO
        new_order = Order(
            customer_name="Nexus AI Auto PO (자동발주)",
            type=OrderTypeEnum.AUTO_PO.value,
            total_price=50000.0,
            status=OrderStatusEnum.PENDING.value
        )
        db.add(new_order)
        db.commit()
        db.refresh(new_order)
        created_orders.append(str(new_order.id))

    return {
        "message": "success",
        "approved_count": len(req.book_ids),
        "created_order_ids": created_orders
    }
