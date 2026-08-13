"""주문 라인에 지정 중고 개체(LPN) 컬럼 추가

중고는 LPN 하나하나가 서로 다른 물건인데 주문에는 book_id만 남아, 할당 엔진이
같은 책의 다른 LPN을 FIFO로 다시 골랐다. 주문한 개체와 지시서에 실리는 개체가
어긋나 스캔 피킹이 실패한다. 선택한 LPN을 주문에 보존한다.

Revision ID: f3c8b2d5a7e1
Revises: e2b7d3f8a1c5
Create Date: 2026-08-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3c8b2d5a7e1"
down_revision: Union[str, Sequence[str], None] = "e2b7d3f8a1c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_items",
        sa.Column("used_item_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # 재고 행이 지워져도 주문 이력은 남아야 하므로 SET NULL.
    op.create_foreign_key(
        "fk_order_items_used_item_id",
        "order_items",
        "inventory_used_items",
        ["used_item_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_order_items_used_item_id", "order_items", type_="foreignkey")
    op.drop_column("order_items", "used_item_id")
