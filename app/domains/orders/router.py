from fastapi import APIRouter, Depends
from sqlmodel import Session
from app.core.database import get_session
from app.models.wms import Order, OrderStatusEnum

router = APIRouter(prefix="/orders", tags=["Orders & Outbound"])

@router.post("/")
def create_order(customer_name: str, type: str, total_price: float, session: Session = Depends(get_session)):
    """주문 생성 및 동적 프라이싱 결과 저장 (Mock)"""
    new_order = Order(
        customer_name=customer_name,
        type=type,
        total_price=total_price,
        status=OrderStatusEnum.PENDING.value
    )
    session.add(new_order)
    session.commit()
    session.refresh(new_order)
    return {"order_id": new_order.id, "message": "주문이 정상 접수되었습니다."}

@router.post("/outbound/pick")
def pick_order(order_id: str):
    """피킹 및 Box Optimization (Mock)"""
    # 3D Bin Packing 알고리즘 연동 예정 (박민우/소한민 파트)
    return {
        "recommended_box": "2호",
        "picking_list": []
    }
