from fastapi import APIRouter, Depends, status
from sqlmodel import Session
from typing import List, Dict, Any
from app.db.session import get_db
from app.models.wms import Order, OrderStatusEnum
from app.domains.inventory.bin_packing import recommend_optimal_box
from app.domains.orders.service import calculate_b2b_price, calculate_dynamic_discount_rate

router = APIRouter(prefix="/orders", tags=["Orders & Outbound"])

@router.post("/", status_code=status.HTTP_201_CREATED)
def create_order(customer_name: str, type: str, list_price: float, category: str, ubci_score: float, days_in_inventory: int, session: Session = Depends(get_db)):
    """동적 프라이싱이 적용된 주문 생성 (AI B2B Price & Discount Rate)"""
    # 1. AI Dynamic Pricing
    base_b2b_price = calculate_b2b_price(list_price, category, ubci_score)
    discount_rate = calculate_dynamic_discount_rate(ubci_score, days_in_inventory, category)
    
    final_total_price = base_b2b_price * (1 - discount_rate)
    
    new_order = Order(
        customer_name=customer_name,
        type=type,
        total_price=final_total_price,
        status=OrderStatusEnum.PENDING.value
    )
    session.add(new_order)
    session.commit()
    session.refresh(new_order)
    
    return {
        "order_id": new_order.id, 
        "base_b2b_price": base_b2b_price,
        "applied_discount_rate": f"{int(discount_rate * 100)}%",
        "final_price": final_total_price,
        "message": "AI 동적 프라이싱 적용 후 주문 접수 완료"
    }

@router.post("/outbound/pick")
def pick_order(order_id: str, books: List[Dict[str, Any]]):
    """피킹 시 3D Bin Packing 알고리즘 기반 박스 최적화"""
    # books example: [{"category": "IT", "format_size": "신국판", "pages": 350, "is_color": False, "is_hardcover": True}]
    recommended_box = recommend_optimal_box(books)
    
    return {
        "order_id": order_id,
        "recommended_box": recommended_box,
        "message": "3D Bin Packing 알고리즘 박스 추천 완료"
    }
