"""LPN 선부착 시점 작업자 사번 컬럼 추가

검수 접수 전(PENDING_INSPECTION) 품목은 ReturnJob이 없어 작업자를 알 수 없었다.
발급 API가 받던 worker_id를 여기 저장해 검수 전 품목에도 작업자를 표시한다.

Revision ID: e2b7d3f8a1c5
Revises: c7f21a9b4e30
Create Date: 2026-08-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2b7d3f8a1c5"
down_revision: Union[str, Sequence[str], None] = "c7f21a9b4e30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "inventory_used_items",
        sa.Column("prelabel_worker_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("inventory_used_items", "prelabel_worker_id")
