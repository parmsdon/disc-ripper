"""discs: add mb_disc_id, mb_toc, mb_lookup_status

Revision ID: 0015_disc_mb_fields
Revises: 0014_catalog_imdb_upc
Create Date: 2026-06-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0015_disc_mb_fields"
down_revision: Union[str, None] = "0014_catalog_imdb_upc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("discs", sa.Column("mb_disc_id", sa.String, nullable=True))
    op.add_column("discs", sa.Column("mb_toc", sa.String, nullable=True))
    op.add_column("discs", sa.Column("mb_lookup_status", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("discs", "mb_lookup_status")
    op.drop_column("discs", "mb_toc")
    op.drop_column("discs", "mb_disc_id")
