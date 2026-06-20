"""drives.pending_action command queue

Revision ID: 0006_drive_pending_action
Revises: 0005_physical_drives_region
Create Date: 2026-06-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0006_drive_pending_action"
down_revision: Union[str, None] = "0005_physical_drives_region"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("drives", sa.Column("pending_action", sa.String, nullable=True))
    op.add_column("drives", sa.Column("pending_action_requested_at", sa.DateTime, nullable=True))


def downgrade() -> None:
    op.drop_column("drives", "pending_action_requested_at")
    op.drop_column("drives", "pending_action")
