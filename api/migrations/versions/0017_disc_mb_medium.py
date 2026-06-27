"""disc_mb_medium: add medium position/count/title columns to discs

Revision ID: 0017_disc_mb_medium
Revises: 0016_rip_log_events
Create Date: 2026-06-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0017_disc_mb_medium"
down_revision: Union[str, None] = "0016_rip_log_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("discs", sa.Column("mb_medium_position", sa.Integer(), nullable=True))
    op.add_column("discs", sa.Column("mb_medium_count", sa.Integer(), nullable=True))
    op.add_column("discs", sa.Column("mb_medium_title", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("discs", "mb_medium_title")
    op.drop_column("discs", "mb_medium_count")
    op.drop_column("discs", "mb_medium_position")
