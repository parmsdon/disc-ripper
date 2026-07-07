"""Add 'protected' value to discstatus PostgreSQL enum.

Revision ID: 0020_disc_protected_status
Revises: 0019_encode_profile_v2
Create Date: 2026-07-07
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0020_disc_protected_status"
down_revision: Union[str, None] = "0019_encode_profile_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE discstatus ADD VALUE IF NOT EXISTS 'protected'")


def downgrade() -> None:
    # Postgres does not support removing enum values without type recreation.
    # To downgrade manually:
    #   UPDATE discs SET status='error', error_message='Was: protected'
    #   WHERE status='protected';
    # Then recreate discstatus without 'protected' (requires column drop/re-add).
    pass
