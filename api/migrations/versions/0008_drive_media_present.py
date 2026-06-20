"""drives.media_present (live disc-presence flag)

Revision ID: 0008_drive_media_present
Revises: 0007_region_as_string
Create Date: 2026-06-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0008_drive_media_present"
down_revision: Union[str, None] = "0007_region_as_string"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("drives", sa.Column("media_present", sa.Boolean, nullable=True))


def downgrade() -> None:
    op.drop_column("drives", "media_present")
