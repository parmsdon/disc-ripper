"""add possible_duplicate_of_id to discs

Revision ID: 0026_disc_possible_duplicate
Revises: 0025_rip_log_event_detail
Create Date: 2026-11-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0026_disc_possible_duplicate'
down_revision: Union[str, None] = '0025_rip_log_event_detail'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('discs', sa.Column('possible_duplicate_of_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'discs_possible_duplicate_of_id_fkey',
        'discs', 'discs',
        ['possible_duplicate_of_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('discs_possible_duplicate_of_id_fkey', 'discs', type_='foreignkey')
    op.drop_column('discs', 'possible_duplicate_of_id')
