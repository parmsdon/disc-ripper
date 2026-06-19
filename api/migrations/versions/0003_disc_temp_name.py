"""disc temp_name column

Revision ID: 0003_disc_temp_name
Revises: 0002_drives_type_nullable
Create Date: 2026-06-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003_disc_temp_name"
down_revision: Union[str, None] = "0002_drives_type_nullable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("discs", sa.Column("temp_name", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("discs", "temp_name")
