"""catalog: add imdb_id and upc

Revision ID: 0014_catalog_imdb_upc
Revises: 0013_disc_rip_quality
Create Date: 2026-06-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0014_catalog_imdb_upc"
down_revision: Union[str, None] = "0013_disc_rip_quality"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("catalog", sa.Column("imdb_id", sa.String, nullable=True))
    op.add_column("catalog", sa.Column("upc", sa.String, nullable=True))
    op.create_index("ix_catalog_imdb_id", "catalog", ["imdb_id"])


def downgrade() -> None:
    op.drop_index("ix_catalog_imdb_id", table_name="catalog")
    op.drop_column("catalog", "upc")
    op.drop_column("catalog", "imdb_id")
