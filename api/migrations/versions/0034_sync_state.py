"""Add sync_state table for DB-backed My Movies sync status.

Revision ID: 0034_sync_state
Revises: 0033_cancelled_status
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_sync_state"
down_revision = "0033_cancelled_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("running", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("progress_current", sa.Integer, nullable=True),
        sa.Column("progress_total", sa.Integer, nullable=True),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("last_synced", sa.Integer, nullable=True),
        sa.Column("last_inserted", sa.Integer, nullable=True),
        sa.Column("last_updated", sa.Integer, nullable=True),
        sa.Column("last_errors", sa.Integer, nullable=True),
        sa.Column("last_duration_seconds", sa.Float, nullable=True),
        sa.Column("last_error_message", sa.Text, nullable=True),
    )
    op.execute("INSERT INTO sync_state (id, running) VALUES (1, FALSE)")


def downgrade() -> None:
    op.drop_table("sync_state")
