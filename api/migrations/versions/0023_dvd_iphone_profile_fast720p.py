"""DVD iPhone profile: switch to Fast 720p30 preset (single AAC stereo track)

Revision ID: 0023_dvd_iphone_profile_fast720p
Revises: 0022_dvd_iphone_profile_720p
Create Date: 2026-11-19

The Apple 720p30 Surround preset defines two audio tracks; extra_args only
override track settings, not track count, leaving an unwanted AC3 surround
track in the output. Fast 720p30 is defined with a single AAC stereo track.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0023_dvd_iphone_profile_fast720p'
down_revision: Union[str, None] = '0022_dvd_iphone_profile_720p'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_PARAMS = '{"preset": "Fast 720p30", "extra_args": ["-q", "25"]}'
_OLD_PARAMS = '{"preset": "Apple 720p30 Surround", "extra_args": ["-q", "25", "--aencoder", "aac", "--mixdown", "stereo"]}'


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
