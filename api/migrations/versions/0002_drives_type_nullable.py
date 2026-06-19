"""drives.drive_type nullable

Revision ID: 0002_drives_type_nullable
Revises: 0001_initial
Create Date: 2026-06-19

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PGEnum


# revision identifiers, used by Alembic.
revision: str = "0002_drives_type_nullable"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

disc_type_enum = PGEnum("cd", "dvd", name="disctype", create_type=False)


def upgrade() -> None:
    op.alter_column("drives", "drive_type", existing_type=disc_type_enum, nullable=True)


def downgrade() -> None:
    op.alter_column("drives", "drive_type", existing_type=disc_type_enum, nullable=False)
