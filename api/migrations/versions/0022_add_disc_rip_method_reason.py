"""add rip_method and rip_reason to discs

Revision ID: 0022_add_disc_rip_method_reason
Revises: fea31f65dd61
Create Date: 2026-11-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0022_add_disc_rip_method_reason'
down_revision: Union[str, None] = 'fea31f65dd61'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('discs', sa.Column('rip_method', sa.String(), nullable=True))
    op.add_column('discs', sa.Column('rip_reason', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('discs', 'rip_reason')
    op.drop_column('discs', 'rip_method')
