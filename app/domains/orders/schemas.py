"""
주문·출고 도메인 요청 스키마.

router.py에서 이관. 라우터가 스키마까지 들고 있으면 service가 타입을
참조할 때 순환 import가 생긴다(service ← router ← service). auth·returns·users·board
슬라이스가 이미 쓰던 구조에 맞춘다.
"""

from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel


class OrderLineRequest(BaseModel):
    # available-books 응답의 id 체계 그대로 수신: "NEW-BOOK-<uuid>"(신품) 또는 중고 item uuid
    id: str
    quantity: int = 1


class CreateOrderRequest(BaseModel):
    customer_name: str = "교보문고 B2B 지점"
    order_type: str = "B2B_ORDER"
    items: List[OrderLineRequest]
    auto_picking_instruction: bool = True  # 주문 즉시 AI 피킹지시서 발행 여부


class AcceptInstructionRequest(BaseModel):
    worker_id: str = "WM2608001"


class PickingScanRequest(BaseModel):
    barcode: str  # LPN(중고) 또는 13자리 ISBN(신품)
    worker_id: str = "WM2608001"
    instruction_id: Optional[UUID] = None  # 미지정 시 활성 지시서 전체에서 매칭


class ConfirmPackingRequest(BaseModel):
    box_id: str
    cushion_name: Optional[str] = None
    force: bool = False  # True면 전량 피킹 전에도 확정 허용 (데모 유연성)


class CompletePackingRequest(BaseModel):
    worker_id: str = "WM2608001"


class OutboundCompleteRequest(BaseModel):
    lpn_barcode: str
    box_type: str
    worker_id: Optional[str] = "WM2608001"


class DynamicPriceRequest(BaseModel):
    list_price: float = 35000
    ubci_score: float = 78
    days_in_inventory: int = 120
    category: str = "Novel"
    title: Optional[str] = None
    isbn: Optional[str] = None


class MultiDynamicPriceRequest(BaseModel):
    items: List[DynamicPriceRequest]
