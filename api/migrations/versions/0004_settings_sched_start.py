"""settings table and rip_jobs.scheduled_start

Revision ID: 0004_settings_sched_start
Revises: 0003_disc_temp_name
Create Date: 2026-06-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0004_settings_sched_start"
down_revision: Union[str, None] = "0003_disc_temp_name"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.String, primary_key=True),
        sa.Column("value", sa.String, nullable=False),
    )
    op.add_column("rip_jobs", sa.Column("scheduled_start", sa.DateTime, nullable=True))


def downgrade() -> None:
    op.drop_column("rip_jobs", "scheduled_start")
    op.drop_table("settings")
