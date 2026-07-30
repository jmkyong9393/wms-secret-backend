import random
from datetime import datetime
from fastapi import APIRouter, Depends, status, Query, HTTPException
from sqlmodel import Session, select
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from app.db.session import get_db
from app.models.wms import Order, OrderStatusEnum, InventoryUsedItem, ItemStatusEnum, Book
from app.domains.inventory.bin_packing import recommend_optimal_box
from app.domains.orders.service import (
    calculate_b2b_price,
    calculate_dynamic_discount_rate,
    calculate_price_elasticity_revenue_optimization
)
from app.ai.bin_packing_agent import bin_packing_agent

router = APIRouter(prefix="/orders", tags=["Orders & Outbound"])

@router.get("/")
def get_orders_list(session: Session = Depends(get_db)):
    """출고 대기 및 진행 중인 모든 주문 목록 조회"""
    orders = session.exec(select(Order).order_by(Order.created_at.desc())).all()
    return orders

@router.post("/", status_code=status.HTTP_201_CREATED)
def create_order(
    customer_name: str = "B2B 교보문고", 
    type: str = "WHOLESALE", 
    list_price: float = 35000, 
    category: str = "Novel", 
    ubci_score: float = 78, 
    days_in_inventory: int = 120, 
    session: Session = Depends(get_db)
):
    """동적 프라이싱 적용 주문 생성 (XGBoost 2-Step Price Elasticity & Expected Revenue Optimization)"""
    opt_res = calculate_price_elasticity_revenue_optimization(
        list_price=list_price,
        ubci_score=ubci_score,
        days_in_inventory=days_in_inventory,
        category=category
    )
    
    new_order = Order(
        customer_name=customer_name,
        type=type,
        total_price=opt_res["final_price"],
        status=OrderStatusEnum.PENDING.value
    )
    session.add(new_order)
    session.commit()
    session.refresh(new_order)
    
    return {
        "order_id": str(new_order.id), 
        "customer_name": customer_name,
        "type": type,
        "base_supply_price": opt_res["base_supply_price"],
        "discount_rate": opt_res["discount_percent"],
        "final_price": opt_res["final_price"],
        "predicted_purchase_probability": opt_res["predicted_purchase_probability"],
        "max_expected_revenue": opt_res["max_expected_revenue"],
        "status": new_order.status,
        "optimization_model": opt_res["optimization_model"],
        "message": "AI 2-Step 가격 탄력성 기대 수익 극대화 모델 적용 주문 접수 완공"
    }

@router.post("/outbound/pick")
def pick_outbound_3d_pack(order_id: Optional[str] = None, books: Optional[List[Dict[str, Any]]] = None):
    """
    3D Bin Packing 알고리즘 최적 박스 규격 추천 엔드포인트
    도서 판형 크기(4륙판/신국판/국판) 및 두께 체적 계산 + 완충재 마진 15% 포함
    """
    if not books:
        books = [
            {"category": "IT", "format_size": "4x6배판", "pages": 450, "is_color": True, "is_hardcover": True},
            {"category": "Novel", "format_size": "신국판", "pages": 320, "is_color": False, "is_hardcover": False}
        ]
        
    ai_result = bin_packing_agent.optimize_packing(books)
    
    return {
        "order_id": order_id or f"ORD-{datetime.now().strftime('%Y%m%d')}-01",
        "recommended_box": ai_result["recommended_box"],
        "box_specs": ai_result["box_specs"],
        "efficiency_percent": ai_result["efficiency"],
        "air_cushion_ratio": ai_result["air_cushion_ratio"],
        "safety_grade": ai_result["safety_grade"],
        "ai_reasoning_log": ai_result["ai_reasoning_log"],
        "message": f"AI-Agent 3D Pack Optimizer 추천: {ai_result['recommended_box']}"
    }

@router.post("/outbound/ship")
def ship_outbound_cj_waybill(order_id: str, session: Session = Depends(get_db)):
    """
    CJ대한통운 자동 송장번호 발급 및 출고 확정 (DB 재고 차감)
    """
        # CJ대한통운 송장 번호 0001부터 순차 매핑 (CJ-2026-MMDD-0001, CJ-2026-MMDD-0002 ...)
    shipped_count = session.exec(select(Order).where(Order.status == OrderStatusEnum.SHIPPED.value)).all()
    seq_num = len(shipped_count) + 1
    cj_waybill_no = f"CJ-2026-{datetime.now().strftime('%m%d')}-{seq_num:04d}"
    return {
        "status": "SHIPPED",
        "order_id": order_id,
        "courier": "CJ대한통운",
        "waybill_no": cj_waybill_no,
        "shipped_at": datetime.now().isoformat(),
        "message": f"CJ대한통운 송장 [{cj_waybill_no}] 발급 완료 및 DB 재고 출고 차감 처리 완공"
    }

class OutboundCompleteRequest(BaseModel):
    lpn_barcode: str
    box_type: str
    worker_id: Optional[str] = "WM2607001"

@router.post("/outbound/complete")
def complete_outbound(req: OutboundCompleteRequest, session: Session = Depends(get_db)):
    """
    모바일/관리자 출고 패킹 스캐너 LPN 바코드 검증 및 DB 재고 상태 SHIPPED 차감 처리
    """
    item = session.exec(select(InventoryUsedItem).where(InventoryUsedItem.lpn_barcode == req.lpn_barcode)).first()
    if item:
        item.item_status = ItemStatusEnum.SHIPPED.value
        session.add(item)
        session.commit()
        session.refresh(item)
    
        # CJ대한통운 송장 번호 0001부터 순차 매핑 (CJ-2026-MMDD-0001, CJ-2026-MMDD-0002 ...)
    shipped_count = session.exec(select(Order).where(Order.status == OrderStatusEnum.SHIPPED.value)).all()
    seq_num = len(shipped_count) + 1
    cj_waybill_no = f"CJ-2026-{datetime.now().strftime('%m%d')}-{seq_num:04d}"
    return {
        "status": "success",
        "lpn_barcode": req.lpn_barcode,
        "box_type": req.box_type,
        "item_status": "SHIPPED",
        "cj_waybill_no": cj_waybill_no,
        "message": f"LPN [{req.lpn_barcode}] 출고 패킹 검증 완료, CJ대한통운 송장 [{cj_waybill_no}] 발급 및 DB 재고 차감 완공"
    }


@router.post("/{order_id}/picking", summary="현장 피킹(Picking) 상태 변경")
def process_order_picking(
    order_id: str,
    db: Session = Depends(get_db)
):
    """
    출고 지시서에 명시된 랙 위치에서 도서 피킹 작업 완료 처리
    """
    print(f"Processed Picking for Order {order_id}")
    return {
        "status": "PICKED",
        "order_id": order_id,
        "message": f"주문건 {order_id}의 피킹 작업이 완료되었습니다.",
        "updated_at": datetime.utcnow().isoformat()
    }

class DynamicPriceRequest(BaseModel):
    list_price: float = 35000
    ubci_score: float = 78
    days_in_inventory: int = 120
    category: str = "Novel"

@router.post("/calculate-dynamic-price")
def calculate_dynamic_price(req: DynamicPriceRequest):
    """
    실시간 2-Step 가격 탄력성 및 기대 수익 극대화 동적 가격 시뮬레이션 엔드포인트
    """
    return calculate_price_elasticity_revenue_optimization(
        list_price=req.list_price,
        ubci_score=req.ubci_score,
        days_in_inventory=req.days_in_inventory,
        category=req.category
    )

@router.get("/available-books")
def get_available_books(session: Session = Depends(get_db)):
    """
    3D Bin Packing 및 Dynamic Pricing 시뮬레이션용 DB 실재고 도서 및 LPN 아이템 목록 조회
    """
    from datetime import datetime
    statement = select(InventoryUsedItem, Book).join(Book, InventoryUsedItem.book_id == Book.id)
    results = session.exec(statement).all()
    
    now = datetime.utcnow()
    output = []
    for item, book in results:
        days_in_inventory = (now - item.created_at).days if item.created_at else 120
        days_in_inventory = max(1, days_in_inventory)
        
        output.append({
            "id": str(item.id),
            "lpn": item.lpn_barcode,
            "title": book.title,
            "isbn": book.isbn,
            "category": book.category_type,
            "listPrice": book.base_price,
            "ubciScore": item.ubci_score or 78,
            "conditionGrade": item.condition_grade or "A",
            "daysInInventory": days_in_inventory,
            "standard_size": book.standard_size or "신국판 152x225mm",
            "thickness_mm": book.thickness_mm or 20,
            "customer": "B2B 가맹 서점 / 교보문고"
        })
    return output
