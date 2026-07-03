"""encode_profile_v2: redesign encode_profiles schema, expand encode_jobs

Replaces the old target/format/params/output_subfolder columns with a
tool-oriented schema (media_type, output_folder, tool, tool_params,
depends_on_profile_id, enabled, display_order).  Drops the encodetarget
PG enum which is no longer needed.

Adds encode_jobs columns required by the encoder service: track_id
(per-track CD jobs), started_at, progress_percent, progress_stage, log.

Revision ID: 0019_encode_profile_v2
Revises: 0018_disc_mb_release_id
Create Date: 2026-07-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PGEnum

revision: str = "0019_encode_profile_v2"
down_revision: Union[str, None] = "0018_disc_mb_release_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

encode_target_enum = PGEnum("audio", "video", name="encodetarget", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()

    # --- encode_profiles: drop old columns ---
    op.drop_constraint("encode_profiles_name_key", "encode_profiles", type_="unique")
    op.drop_column("encode_profiles", "target")
    op.drop_column("encode_profiles", "format")
    op.drop_column("encode_profiles", "params")
    op.drop_column("encode_profiles", "output_subfolder")

    encode_target_enum.drop(bind, checkfirst=True)

    # --- encode_profiles: add new columns ---
    # server_default="" on the three required string fields handles any
    # pre-existing seeded rows; seed.py will overwrite them with real values.
    op.add_column("encode_profiles", sa.Column("media_type", sa.String(), nullable=False, server_default="dvd"))
    op.add_column("encode_profiles", sa.Column("output_folder", sa.String(), nullable=False, server_default=""))
    op.add_column("encode_profiles", sa.Column("tool", sa.String(), nullable=False, server_default=""))
    op.add_column("encode_profiles", sa.Column("tool_params", sa.Text(), nullable=True))
    op.add_column("encode_profiles", sa.Column("depends_on_profile_id", sa.Integer(), nullable=True))
    op.add_column("encode_profiles", sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("encode_profiles", sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"))

    op.create_foreign_key(
        "fk_encode_profiles_depends_on",
        "encode_profiles", "encode_profiles",
        ["depends_on_profile_id"], ["id"],
    )

    # --- encode_jobs: add new columns ---
    op.add_column("encode_jobs", sa.Column("track_id", sa.Integer(), nullable=True))
    op.add_column("encode_jobs", sa.Column("started_at", sa.DateTime(), nullable=True))
    op.add_column("encode_jobs", sa.Column("progress_percent", sa.Integer(), nullable=True))
    op.add_column("encode_jobs", sa.Column("progress_stage", sa.String(), nullable=True))
    op.add_column("encode_jobs", sa.Column("log", sa.Text(), nullable=True))

    op.create_foreign_key(
        "fk_encode_jobs_track_id",
        "encode_jobs", "cd_tracks",
        ["track_id"], ["id"],
    )


def downgrade() -> None:
    bind = op.get_bind()

    # --- encode_jobs: remove new columns ---
    op.drop_constraint("fk_encode_jobs_track_id", "encode_jobs", type_="foreignkey")
    op.drop_column("encode_jobs", "log")
    op.drop_column("encode_jobs", "progress_stage")
    op.drop_column("encode_jobs", "progress_percent")
    op.drop_column("encode_jobs", "started_at")
    op.drop_column("encode_jobs", "track_id")

    # --- encode_profiles: remove new columns ---
    op.drop_constraint("fk_encode_profiles_depends_on", "encode_profiles", type_="foreignkey")
    op.drop_column("encode_profiles", "display_order")
    op.drop_column("encode_profiles", "enabled")
    op.drop_column("encode_profiles", "depends_on_profile_id")
    op.drop_column("encode_profiles", "tool_params")
    op.drop_column("encode_profiles", "tool")
    op.drop_column("encode_profiles", "output_folder")
    op.drop_column("encode_profiles", "media_type")

    # Restore encodetarget enum and old columns (nullable on downgrade)
    encode_target_enum.create(bind, checkfirst=True)
    op.add_column("encode_profiles", sa.Column("target", encode_target_enum, nullable=True))
    op.add_column("encode_profiles", sa.Column("format", sa.String(), nullable=True))
    op.add_column("encode_profiles", sa.Column("params", sa.JSON(), nullable=True))
    op.add_column("encode_profiles", sa.Column("output_subfolder", sa.String(), nullable=True))
    op.create_unique_constraint("encode_profiles_name_key", "encode_profiles", ["name"])
