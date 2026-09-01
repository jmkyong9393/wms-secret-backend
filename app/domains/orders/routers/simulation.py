"""B2B 주문 시뮬레이션(데모)

orders/router.py 1,121줄 분할(2026-09-01). 본문은 원본에서 그대로 이동 - 수정 금지 원칙.
URL·인증·동작 전부 불변이며, 분할 전후 OpenAPI 경로 스냅샷 일치로 증명했다.
"""

import logging
import random
from typing import Dict, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.core.security import RoleChecker
from app.db.session import get_db

# 원본에서는 같은 파일의 라우트 함수를 직접 호출했다 - 분할로 파일이 갈려 import로 잇는다.
from app.domains.orders.routers.orders_crud import create_order_with_items
from app.domains.orders.schemas import (
    CreateOrderRequest,
    OrderLineRequest,
)
from app.models.wms import (
    Book,
    InventoryUsedItem,
    ItemStatusEnum,
    UserRoleEnum,
)

logger = logging.getLogger(__name__)
# 라우터 전체에 인증을 건다. 엔드포인트마다 붙이면 새 경로를 추가할 때 또 빠뜨린다 -
# 실제로 재고·피킹지시서·발주제안이 무인증으로 조회되던 것을 전수 점검에서 발견했다.
# 주문·피킹·출고는 로그인 필수
# 부모(orders/router.py)가 prefix·인증 의존성을 보유한다 - 여기서는 경로만 정의한다.
router = APIRouter()

# 되돌리기 같은 재고 변경 작업은 관리자만 수행한다.
admin_only = RoleChecker([UserRoleEnum.MASTER, UserRoleEnum.ADMIN])


@router.post("/simulate-b2b", status_code=status.HTTP_201_CREATED)
def simulate_b2b_order(session: Session = Depends(get_db)):
    """
    B2B 묶음 주문 랜덤 시뮬레이션 - DB 실재고에서 2~4종을 무작위 선택해
    실제 Order + OrderItem + AI 피킹 지시서를 생성한다. (기존 mock 버튼 대체)
    """
    customers = [
        "교보문고 B2B 지점",
        "알라딘 중고매장 강남점",
        "예스24 B2B 물류센터",
        "영풍문고 종로본점",
    ]

    # 종전에는 활성 도서 전체에서 무작위로 뽑아, 재고가 0인 신품도
    # 주문에 실렸다(중고는 IN_STOCK 조건이 있었으나 신품은 아무 조건이 없었다).
    # 시뮬레이션이라도 팔 수 없는 물건을 주문에 넣으면 그 뒤 흐름 전체가 거짓이 된다.
    from app.domains.inventory.service import get_new_stock_map

    stock_by_book = get_new_stock_map(session)
    new_books = [
        b
        for b in session.exec(select(Book).where(Book.is_active == True)).all()
        if stock_by_book.get(b.id, 0) > 0
    ]
    used_items = session.exec(
        select(InventoryUsedItem).where(
            InventoryUsedItem.item_status == ItemStatusEnum.IN_STOCK.value
        )
    ).all()

    picks: List[OrderLineRequest] = []
    if new_books:
        for b in random.sample(new_books, min(len(new_books), random.randint(1, 2))):
            # 보유 수량을 넘겨 주문하지 않는다.
            available = stock_by_book.get(b.id, 0)
            picks.append(
                OrderLineRequest(
                    id=f"NEW-BOOK-{b.id}", quantity=random.randint(1, min(3, available))
                )
            )
    if used_items:
        # 도서 단위로 뽑는다. LPN 행 단위로 뽑으면 재고가 많은 책이 보유 수량에 비례해
        # 당첨돼(20권 보유 = 1권 보유의 20배 확률) 시뮬레이션이 매번 같은 책을 낸다.
        by_book: Dict[UUID, List[InventoryUsedItem]] = {}
        for u in used_items:
            by_book.setdefault(u.book_id, []).append(u)
        for book_id in random.sample(
            list(by_book), min(len(by_book), random.randint(1, 2))
        ):
            chosen = random.choice(by_book[book_id])
            picks.append(OrderLineRequest(id=str(chosen.id), quantity=1))
    if not picks:
        raise HTTPException(409, "시뮬레이션에 사용할 재고가 없습니다.")

    return create_order_with_items(
        CreateOrderRequest(customer_name=random.choice(customers), items=picks),
        session=session,
    )
