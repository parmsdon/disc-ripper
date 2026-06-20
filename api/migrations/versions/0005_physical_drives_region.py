"""physical drives and region tracking

Revision ID: 0005_physical_drives_region
Revises: 0004_settings_sched_start
Create Date: 2026-06-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0005_physical_drives_region"
down_revision: Union[str, None] = "0004_settings_sched_start"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "physical_drives",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("hardware_id", sa.String, nullable=False, unique=True),
        sa.Column("region", sa.Integer, nullable=True),
        sa.Column("region_known", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("last_seen_at", sa.DateTime, nullable=True),
        sa.Column("notes", sa.String, nullable=True),
    )
    op.add_column(
        "drives",
        sa.Column("physical_drive_id", sa.Integer, sa.ForeignKey("physical_drives.id"), nullable=True),
    )
    op.add_column("discs", sa.Column("ripped_in_region", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("discs", "ripped_in_region")
    op.drop_column("drives", "physical_drive_id")
    op.drop_table("physical_drives")
