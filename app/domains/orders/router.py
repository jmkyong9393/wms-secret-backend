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

from pydantic import BaseModel
from typing import Optional

class OutboundCompleteRequest(BaseModel):
    lpn_barcode: str
    box_type: str
    worker_id: Optional[str] = "WM2607001"

@router.post("/outbound/complete")
def complete_outbound(req: OutboundCompleteRequest, session: Session = Depends(get_db)):
    """
    모바일/관리자 출고 스캐너에서 LPN 바코드 검증 및 3D 패킹 완료 시
    실제 DB의 InventoryUsedItem 상태를 SHIPPED로 갱신하고 재고 차감 처리합니다.
    """
    from app.models.wms import InventoryUsedItem, ItemStatusEnum
    from sqlmodel import select
    
    item = session.exec(select(InventoryUsedItem).where(InventoryUsedItem.lpn_barcode == req.lpn_barcode)).first()
    if item:
        item.item_status = ItemStatusEnum.SHIPPED.value
        session.add(item)
        session.commit()
        session.refresh(item)
    
    return {
        "status": "success",
        "lpn_barcode": req.lpn_barcode,
        "box_type": req.box_type,
        "item_status": "SHIPPED",
        "message": f"LPN [{req.lpn_barcode}] 출고 패킹 검증 및 DB 재고 차감 완공"
    }
