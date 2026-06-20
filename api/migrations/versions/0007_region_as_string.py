"""region stored as space-separated digit string, not a single integer

regionset can report a drive as supporting multiple regions (region-free
or multi-region drives), so PhysicalDrive.region and Disc.ripped_in_region
move from Integer to String.

Revision ID: 0007_region_as_string
Revises: 0006_drive_pending_action
Create Date: 2026-06-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0007_region_as_string"
down_revision: Union[str, None] = "0006_drive_pending_action"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "physical_drives", "region",
        existing_type=sa.Integer(),
        type_=sa.String(),
        postgresql_using="region::varchar",
    )
    op.alter_column(
        "discs", "ripped_in_region",
        existing_type=sa.Integer(),
        type_=sa.String(),
        postgresql_using="ripped_in_region::varchar",
    )


def downgrade() -> None:
    # Lossy for multi-region values (e.g. "1 2 3 4 5 6 7 8") - only the
    # first listed region survives the round trip back to a single integer.
    op.alter_column(
        "discs", "ripped_in_region",
        existing_type=sa.String(),
        type_=sa.Integer(),
        postgresql_using="NULLIF(split_part(ripped_in_region, ' ', 1), '')::integer",
    )
    op.alter_column(
        "physical_drives", "region",
        existing_type=sa.String(),
        type_=sa.Integer(),
        postgresql_using="NULLIF(split_part(region, ' ', 1), '')::integer",
    )
