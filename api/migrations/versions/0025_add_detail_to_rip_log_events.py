"""add detail column to rip_log_events

Revision ID: 0025_add_detail_to_rip_log_events
Revises: 0024_add_disc_rip_method_reason
Create Date: 2026-11-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0025_add_detail_to_rip_log_events'
down_revision: Union[str, None] = '0024_add_disc_rip_method_reason'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('rip_log_events', sa.Column('detail', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('rip_log_events', 'detail')
