"""add volume_label to discs

Revision ID: 0027_disc_volume_label
Revises: 0026_disc_possible_duplicate
Create Date: 2026-11-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0027_disc_volume_label'
down_revision: Union[str, None] = '0026_disc_possible_duplicate'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('discs', sa.Column('volume_label', sa.String(), nullable=True))

    # Backfill: for all discs created before the content-based ID was introduced,
    # disc_fingerprint held the plain ISO 9660 volume label. Carry that value over
    # so ISO file naming still works for existing rips.
    op.execute(
        "UPDATE discs SET volume_label = disc_fingerprint "
        "WHERE disc_fingerprint IS NOT NULL "
        "AND disc_fingerprint NOT LIKE 'ifo:%'"
    )


def downgrade() -> None:
    op.drop_column('discs', 'volume_label')
