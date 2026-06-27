"""disc_mb_release_id: add mb_release_id column to discs

Revision ID: 0018_disc_mb_release_id
Revises: 0017_disc_mb_medium
Create Date: 2026-06-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0018_disc_mb_release_id"
down_revision: Union[str, None] = "0017_disc_mb_medium"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("discs", sa.Column("mb_release_id", sa.String(), nullable=True))
    op.create_index("ix_discs_mb_release_id", "discs", ["mb_release_id"])


def downgrade() -> None:
    op.drop_index("ix_discs_mb_release_id", table_name="discs")
    op.drop_column("discs", "mb_release_id")
