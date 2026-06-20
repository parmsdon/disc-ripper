"""drives.tray_open

Revision ID: 0009_drive_tray_open
Revises: 0008_drive_media_present
Create Date: 2026-06-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0009_drive_tray_open"
down_revision: Union[str, None] = "0008_drive_media_present"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("drives", sa.Column("tray_open", sa.Boolean, nullable=True))


def downgrade() -> None:
    op.drop_column("drives", "tray_open")
