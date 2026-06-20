"""rip_jobs progress columns

Revision ID: 0010_ripjob_progress
Revises: 0009_drive_tray_open
Create Date: 2026-06-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0010_ripjob_progress"
down_revision: Union[str, None] = "0009_drive_tray_open"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("rip_jobs", sa.Column("progress_percent", sa.Integer, nullable=True))
    op.add_column("rip_jobs", sa.Column("progress_stage", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("rip_jobs", "progress_stage")
    op.drop_column("rip_jobs", "progress_percent")
