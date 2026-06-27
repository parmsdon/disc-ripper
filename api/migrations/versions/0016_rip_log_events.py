"""rip_log_events: activity log table for disc/track ripping events

Revision ID: 0016_rip_log_events
Revises: 0015_disc_mb_fields
Create Date: 2026-06-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0016_rip_log_events"
down_revision: Union[str, None] = "0015_disc_mb_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rip_log_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("occurred_at", sa.DateTime, nullable=False),
        sa.Column("drive_label", sa.String, nullable=True),
        sa.Column("disc_id", sa.Integer, nullable=True),
        sa.Column("working_title", sa.String, nullable=True),
        sa.Column("track_number", sa.Integer, nullable=True),
        sa.Column("event_type", sa.String, nullable=False),
        sa.Column("outcome", sa.String, nullable=True),
        sa.Column("elapsed_seconds", sa.Float, nullable=True),
        sa.ForeignKeyConstraint(["disc_id"], ["discs.id"], ondelete="SET NULL"),
    )


def downgrade() -> None:
    op.drop_table("rip_log_events")
