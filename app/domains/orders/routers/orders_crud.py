"""주문 생성·조회·상태, 동적 가격, 출고 가능 도서

orders/router.py 1,121줄 분할(2026-09-01). 본문은 원본에서 그대로 이동 - 수정 금지 원칙.
URL·인증·동작 전부 불변이며, 분할 전후 OpenAPI 경로 스냅샷 일치로 증명했다.
"""

import logging
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlmodel import Session, select

from app.core.security import RoleChecker
from app.db.session import get_db
from app.domains.orders.picking import (
    create_picking_instruction,
    serialize_instruction,
)
from app.domains.orders.schemas import (
    CreateOrderRequest,
)
from app.domains.orders.service import (
    _resolve_order_lines,
    calculate_order_pricing,
    calculate_price_elasticity_revenue_optimization,
)
from app.models.wms import (
    Book,
    InventoryUsedItem,
    ItemStatusEnum,
    Order,
    OrderItem,
    OrderStatusEnum,
    PickingInstructionItem,
    UserRoleEnum,
    now_kst,
)

logger = logging.getLogger(__name__)
# 라우터 전체에 인증을 건다. 엔드포인트마다 붙이면 새 경로를 추가할 때 또 빠뜨린다 -
# 실제로 재고·피킹지시서·발주제안이 무인증으로 조회되던 것을 전수 점검에서 발견했다.
# 주문·피킹·출고는 로그인 필수
# 부모(orders/router.py)가 prefix·인증 의존성을 보유한다 - 여기서는 경로만 정의한다.
router = APIRouter()

# 되돌리기 같은 재고 변경 작업은 관리자만 수행한다.
admin_only = RoleChecker([UserRoleEnum.MASTER, UserRoleEnum.ADMIN])


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
    session: Session = Depends(get_db),
):
    """동적 프라이싱 적용 주문 생성 — XGBoost 구매확률 예측 + 기대매출 최대화 그리드 탐색"""
    opt_res = calculate_price_elasticity_revenue_optimization(
        list_price=list_price,
        ubci_score=ubci_score,
        days_in_inventory=days_in_inventory,
        category=category,
    )

    new_order = Order(
        customer_name=customer_name,
        type=type,
        total_price=opt_res["final_price"],
        status=OrderStatusEnum.PENDING.value,
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
        "message": "AI 2-Step 가격 탄력성 기대 수익 극대화 모델 적용 주문 접수 완공",
    }


# ==========================================
# Order → AI 피킹 지시서 → 출고 파이프라인
# ==========================================


@router.post("/create-with-items", status_code=status.HTTP_201_CREATED)
def create_order_with_items(
    req: CreateOrderRequest, session: Session = Depends(get_db)
):
    """
    실제 Order + OrderItem 생성 엔드포인트.
    라인별 신품(도서정가제 10%)/중고(탄력성 모델) 가격을 주문 시점에 확정 저장하고,
    옵션에 따라 AI 피킹 지시서까지 즉시 발행한다.
    """
    if not req.items:
        raise HTTPException(400, "주문 항목이 비어 있습니다.")

    lines = _resolve_order_lines(session, req.items)
    pricing = calculate_order_pricing(
        [
            {
                "is_new": ln["is_new"],
                "list_price": ln["book"].base_price or 15000,
                "ubci_score": ln["ubci_score"],
                "days_in_inventory": ln["days_in_inventory"],
                "category": ln["book"].category_type or "GENERAL",
                "quantity": ln["quantity"],
                "title": ln["book"].title,
                "isbn": ln["book"].isbn,
            }
            for ln in lines
        ]
    )

    order = Order(
        customer_name=req.customer_name,
        type=req.order_type,
        total_price=pricing["final_price"],
        status=OrderStatusEnum.PENDING.value,
    )
    session.add(order)
    session.flush()

    for ln, priced in zip(lines, pricing["lines"]):
        session.add(
            OrderItem(
                order_id=order.id,
                book_id=ln["book"].id,
                quantity=ln["quantity"],
                unit_price=priced["unit_price"],
                condition_pref="NEW" if ln["is_new"] else "USED",
                used_item_id=ln["used_item"].id if ln.get("used_item") else None,
            )
        )
    session.commit()
    session.refresh(order)

    instruction_payload = None
    if req.auto_picking_instruction:
        instruction = create_picking_instruction(session, order)
        instruction_payload = serialize_instruction(session, instruction)

    return {
        "order_id": str(order.id),
        "customer_name": order.customer_name,
        "status": order.status,
        "pricing": pricing,
        "picking_instruction": instruction_payload,
        "message": f"주문 접수 완료 (총 {pricing['total_quantity']}권 / {pricing['final_price']:,.0f}원)"
        + (" - AI 피킹 지시서 발행 완료" if instruction_payload else ""),
    }


@router.post("/{order_id}/picking", summary="현장 피킹(Picking) 상태 변경")
def process_order_picking(order_id: str, db: Session = Depends(get_db)):
    """
    출고 지시서에 명시된 랙 위치에서 도서 피킹 작업 완료 처리
    """
    logger.info(f"Processed Picking for Order {order_id}")
    return {
        "status": "PICKED",
        "order_id": order_id,
        "message": f"주문건 {order_id}의 피킹 작업이 완료되었습니다.",
        "updated_at": now_kst().isoformat(),
    }


@router.post("/calculate-dynamic-price")
def calculate_dynamic_price(req: Dict[str, Any]):
    """
    실시간 동적 가격 시뮬레이션 (Two-Track: 신품 도서정가제 정율 / 중고 2-Step 탄력성 모델)
    - 라인별 quantity(수량), is_new(신품 여부), ubci_score(null 안전) 반영
    - 총액 = Σ(권당 확정가 x 수량) - 이전 버전의 '전체를 중고 평균으로 뭉개는' 연산 제거
    """
    items = req.get("items")
    if items and isinstance(items, list) and len(items) > 0:
        pricing = calculate_order_pricing(items)
        # 기존 프론트 호환 필드 유지
        max_days = max((item.get("days_in_inventory") or 1) for item in items)
        dwell_decay = round(min(max_days, 365) / 365.0 * 0.10, 3)
        pricing["trend_badge_text"] = pricing["pricing_label"]
        pricing["dwell_badge_text"] = (
            f"비부패성 보관료 방어: -{round(dwell_decay * 100, 1)}% ({max_days}일 체류)"
        )
        pricing["item_count"] = len(items)
        return pricing
    else:
        list_price = req.get("list_price", 35000)
        ubci_score = req.get("ubci_score", 78)
        days_in_inventory = req.get("days_in_inventory", 120)
        category = req.get("category", "Novel")
        return calculate_price_elasticity_revenue_optimization(
            list_price=list_price,
            ubci_score=ubci_score if ubci_score is not None else 85,
            days_in_inventory=days_in_inventory,
            category=category,
        )


# [미사용/확장예정] 아래 판형 카탈로그는 현재 참조처가 없다 - 3D 적재 고도화용 도메인 지식이라 존치 (전역 grep 0건, 2026-09-01)
CATEGORY_DEFAULT_SPECS = {
    "Comic": {
        "name": "B6 (46판 만화)",
        "w": 128.0,
        "d": 188.0,
        "pages": 200,
        "cover_h": 2.0,
    },
    "Novel": {
        "name": "A5 (국판 소설)",
        "w": 148.0,
        "d": 210.0,
        "pages": 320,
        "cover_h": 2.0,
    },
    "Economy": {
        "name": "신국판 (경제/자기계발)",
        "w": 152.0,
        "d": 223.0,
        "pages": 380,
        "cover_h": 2.0,
    },
    "SelfHelp": {
        "name": "신국판 (자기계발)",
        "w": 152.0,
        "d": 223.0,
        "pages": 380,
        "cover_h": 2.0,
    },
    "Humanity": {
        "name": "신국판 (인문)",
        "w": 152.0,
        "d": 223.0,
        "pages": 360,
        "cover_h": 2.0,
    },
    "IT": {
        "name": "B5 (46배판 IT기술서)",
        "w": 188.0,
        "d": 257.0,
        "pages": 480,
        "cover_h": 2.0,
    },
    "Textbook": {
        "name": "B5 (46배판 문제집)",
        "w": 188.0,
        "d": 257.0,
        "pages": 480,
        "cover_h": 2.0,
    },
    "Language": {
        "name": "B5 (외국어/토익)",
        "w": 188.0,
        "d": 257.0,
        "pages": 520,
        "cover_h": 2.0,
    },
    "Child": {
        "name": "A4 (아동/화보)",
        "w": 210.0,
        "d": 297.0,
        "pages": 120,
        "cover_h": 2.0,
    },
    "Magazine": {
        "name": "A4 (잡지)",
        "w": 210.0,
        "d": 297.0,
        "pages": 160,
        "cover_h": 2.0,
    },
    "GENERAL": {
        "name": "신국판 표준",
        "w": 152.0,
        "d": 223.0,
        "pages": 350,
        "cover_h": 2.0,
    },
}


@router.get("/available-books")
def get_available_books(
    response: Response,
    instruction_id: Optional[UUID] = Query(
        None, description="이 지시서에 할당된 중고 LPN도 후보에 포함"
    ),
    session: Session = Depends(get_db),
):
    """
    3D Bin Packing 및 Dynamic Pricing 시뮬레이션용 DB 실재고 도서
    - 1순위: DB books 테이블의 신품 도서 44권 (LPN 미발급, Zone A)
    - 2순위: DB inventory_used_items 테이블의 중고 도서 (LPN 바코드, Zone B/C/D)
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    output = []
    now = now_kst()

    # 1. 신품 - 활성 도서 중 실보유 수량이 있는 것만. 카탈로그 전체가 아니다.
    from app.domains.inventory.service import get_new_stock_map

    new_stock_map = get_new_stock_map(session)

    # 포장 확정(confirm-packing) 시 신품은 inventory.quantity에서 차감된다. 그래서
    # 이미 처리된 지시서를 다시 열면 그 신품이 재고 0이 되어 목록에서 빠지고,
    # **중고 라인만 남아 "신품 0권"으로 가격이 산정된다.**
    # 중고(ALLOCATED)는 아래에서 지시서 소속 개체를 되살리는데 신품에는 그 대칭이
    # 없었다 - 여기서 지시서에 걸린 신품 수량을 후보에 되돌린다.
    instruction_new_qty: Dict[UUID, int] = {}
    if instruction_id:
        for row in session.exec(
            select(PickingInstructionItem).where(
                PickingInstructionItem.instruction_id == instruction_id
            )
        ).all():
            if (row.stock_type or "").upper() == "NEW" and row.book_id:
                instruction_new_qty[row.book_id] = instruction_new_qty.get(
                    row.book_id, 0
                ) + int(row.quantity or 0)

    all_books = session.exec(select(Book).where(Book.is_active == True)).all()
    for idx, b in enumerate(all_books):
        pure_db_stock_qty = new_stock_map.get(b.id, 0) + instruction_new_qty.get(
            b.id, 0
        )
        if pure_db_stock_qty <= 0:
            continue

        output.append(
            {
                "id": f"NEW-BOOK-{b.id}",
                "lpn": "LPN 미발급 (신품)",
                "title": b.title,
                "author": b.author or "-",
                "publisher": b.publisher or "-",
                "isbn": b.isbn,
                "cover_image_url": b.cover_image_url or "",
                "category": b.category_type or "GENERAL",
                "listPrice": b.base_price or 15000,
                "ubciScore": None,
                "conditionGrade": "NEW_FASTTRACK",
                "isNew": True,
                "daysInInventory": 1,
                "stock_qty": pure_db_stock_qty,
                "width_mm": b.width_mm or 152.0,
                "depth_mm": b.depth_mm or 223.0,
                "thickness_mm": b.thickness_mm or 20.0,
                "weight_g": b.weight_g or 500.0,
                "calc_source": "REAL_DB_BOOKS_MASTER",
                "customer": "B2B 가맹 서점 / 교보문고",
            }
        )

    # 2. Fetch USED items from inventory_used_items (Each LPN item is uniquely 1-qty)
    #
    # 검수가 끝나 등급이 확정된 품목만 판매 가능 재고로 취급한다. 선부착 대기
    # (PENDING_INSPECTION)와 결재 대기(HITL_*)는 아직 팔 수 없고 등급·점수도 없으므로
    # 동적 가격 산정과 3D 적재 시뮬레이션 입력에서 제외한다.
    # 판매 가능 상태만 허용 목록으로 명시한다 (거부 목록은 SHIPPED를 놓쳤다).
    from sqlalchemy import or_

    SELLABLE = [ItemStatusEnum.IN_STOCK.value]
    conditions = [
        InventoryUsedItem.item_status.is_(None),
        InventoryUsedItem.item_status.in_(SELLABLE),
    ]

    # 지시서 발행 시 중고 LPN은 ALLOCATED로 잠기므로 판매 가능 목록에서 빠진다.
    # 출고 화면이 그 지시서를 열 때는 잠긴 개체도 후보에 넣어야 한다 -
    # 아니면 중고 라인이 조용히 누락된 채 신품만으로 가격이 산정된다.
    if instruction_id:
        allocated_ids = {
            row.used_item_id
            for row in session.exec(
                select(PickingInstructionItem).where(
                    PickingInstructionItem.instruction_id == instruction_id
                )
            ).all()
            if row.used_item_id
        }
        if allocated_ids:
            conditions.append(InventoryUsedItem.id.in_(allocated_ids))

    used_stmt = (
        select(InventoryUsedItem, Book)
        .join(Book, InventoryUsedItem.book_id == Book.id)
        .where(or_(*conditions))
    )
    used_results = session.exec(used_stmt).all()

    for idx, (item, b) in enumerate(used_results):
        days_in_inventory = (
            (now - item.created_at).days if getattr(item, "created_at", None) else 120
        )
        days_in_inventory = max(1, days_in_inventory)

        output.append(
            {
                "id": str(item.id),
                "lpn": item.lpn_barcode,
                "title": b.title,
                "author": b.author or "-",
                "publisher": b.publisher or "-",
                "isbn": b.isbn,
                "cover_image_url": b.cover_image_url or "",
                "category": b.category_type or "GENERAL",
                "listPrice": b.base_price or 15000,
                "ubciScore": item.ubci_score,
                "conditionGrade": item.condition_grade or None,
                "isNew": False,
                "daysInInventory": days_in_inventory,
                "stock_qty": 1,
                "width_mm": b.width_mm or 152.0,
                "depth_mm": b.depth_mm or 223.0,
                "thickness_mm": b.thickness_mm or 20.0,
                "weight_g": b.weight_g or 500.0,
                "calc_source": "REAL_DB_USED_INVENTORY",
                "customer": "B2B 가맹 서점 / 교보문고",
            }
        )

    return output
