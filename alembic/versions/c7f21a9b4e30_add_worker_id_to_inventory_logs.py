"""add worker_id to inventory_logs

신품 Fast-Track 입고의 작업자를 기록할 칸을 만든다. 종전에는 저장 위치가 없어
"나의 검수 내역"이 신품 입고분을 필터링할 기준값 자체를 갖지 못했다.

Revision ID: c7f21a9b4e30
Revises: b43da7d61022
Create Date: 2026-08-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c7f21a9b4e30'
down_revision: Union[str, Sequence[str], None] = 'b43da7d61022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 기존 행은 작업자를 소급할 방법이 없으므로 nullable로 둔다 (NULL = 미기록).
    op.add_column('inventory_logs', sa.Column('worker_id', sa.String(length=50), nullable=True))
    op.create_index(op.f('ix_inventory_logs_worker_id'), 'inventory_logs', ['worker_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_inventory_logs_worker_id'), table_name='inventory_logs')
    op.drop_column('inventory_logs', 'worker_id')
