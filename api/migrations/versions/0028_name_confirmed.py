"""add name_confirmed to discs

Revision ID: 0028_name_confirmed
Revises: fea31f65dd61
Create Date: 2026-11-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_name_confirmed"
down_revision: Union[str, None] = "0027_disc_volume_label"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "discs",
        sa.Column("name_confirmed", sa.Boolean(), nullable=False, server_default="false"),
    )
    # Discs that already have a catalog match or are fully done are considered confirmed.
    op.execute(
        "UPDATE discs SET name_confirmed = TRUE "
        "WHERE catalog_id IS NOT NULL OR status = 'done'::discstatus"
    )
    op.alter_column("discs", "name_confirmed", server_default=None)


def downgrade() -> None:
    op.drop_column("discs", "name_confirmed")
