"""cd_mp3_depends_on_flac: set depends_on_profile_id on CD MP3 320 → CD FLAC

MP3 encodes now read from the FLAC output rather than the raw WAV, so the
CD MP3 320 profile must declare its dependency on CD FLAC. This migration
fixes existing environments where seed.py left depends_on_profile_id NULL.

Revision ID: 0021_cd_mp3_depends_on_flac
Revises: 0020_disc_protected_status
Create Date: 2026-10-18

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0021_cd_mp3_depends_on_flac"
down_revision: Union[str, None] = "0020_disc_protected_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        UPDATE encode_profiles
        SET depends_on_profile_id = (
            SELECT id FROM encode_profiles WHERE name = 'CD FLAC'
        )
        WHERE name = 'CD MP3 320'
          AND depends_on_profile_id IS NULL
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE encode_profiles
        SET depends_on_profile_id = NULL
        WHERE name = 'CD MP3 320'
          AND depends_on_profile_id = (
              SELECT id FROM encode_profiles WHERE name = 'CD FLAC'
          )
    """)
