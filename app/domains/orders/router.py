"""주문·피킹·출고 라우터 - 조립 전용.

1,121줄 단일 파일을 관심사별 4모듈로 분할했다(2026-09-01). 이 파일은 prefix와
전체 인증 의존성만 보유하고 라우트는 routers/ 하위 모듈이 정의한다.
경로·인증·동작은 분할 전과 동일하며 OpenAPI 스냅샷 일치로 증명했다.
"""

from fastapi import APIRouter, Depends

from app.core.security import get_current_user
from app.domains.orders.routers import (
    orders_crud,
    outbound_scan,
    picking_instructions,
    simulation,
)

# 라우터 전체에 인증을 건다. 엔드포인트마다 붙이면 새 경로를 추가할 때 또 빠뜨린다 -
# 실제로 재고·피킹지시서·발주제안이 무인증으로 조회되던 것을 전수 점검에서 발견했다.
router = APIRouter(
    prefix="/orders",
    tags=["Orders & Outbound"],
    dependencies=[Depends(get_current_user)],
)

router.include_router(orders_crud.router)
router.include_router(picking_instructions.router)
router.include_router(outbound_scan.router)
router.include_router(simulation.router)
