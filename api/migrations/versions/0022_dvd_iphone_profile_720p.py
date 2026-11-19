"""DVD iPhone profile: switch to Apple 720p30 with quality/audio overrides

Revision ID: 0022_dvd_iphone_profile_720p
Revises: d1aedc495665
Create Date: 2026-11-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0022_dvd_iphone_profile_720p'
down_revision: Union[str, None] = 'd1aedc495665'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_PARAMS = '{"preset": "Apple 720p30 Surround", "extra_args": ["-q", "25", "--aencoder", "aac", "--mixdown", "stereo"]}'
_OLD_PARAMS = '{"preset": "Apple 1080p30 Surround"}'


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE encode_profiles SET tool_params = :new WHERE name = 'DVD iPhone'"
        ).bindparams(new=_NEW_PARAMS)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE encode_profiles SET tool_params = :old WHERE name = 'DVD iPhone'"
        ).bindparams(old=_OLD_PARAMS)
    )
