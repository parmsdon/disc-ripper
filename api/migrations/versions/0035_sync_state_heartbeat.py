"""Add progress_updated_at to sync_state for stale-lock detection.

Revision ID: 0035_sync_state_heartbeat
Revises: 0034_sync_state
"""

import sqlalchemy as sa
from alembic import op

revision = "0035_sync_state_heartbeat"
down_revision = "0034_sync_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sync_state",
        sa.Column("progress_updated_at", sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sync_state", "progress_updated_at")
