"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PGEnum


# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enum type names match common/models.py
def _enum(*values, name):
    return PGEnum(*values, name=name, create_type=False)

disc_type_enum = _enum("cd", "dvd", name="disctype")
disc_status_enum = _enum("queued", "ripping", "ripped", "encoding", "done", "error", name="discstatus")
job_status_enum = _enum("queued", "running", "done", "error", name="jobstatus")
rip_quality_enum = _enum("good", "imperfect", "failed", name="ripquality")
media_type_enum = _enum("movie", "series", name="mediatype")
encode_target_enum = _enum("audio", "video", name="encodetarget")

ALL_ENUMS = [
    disc_type_enum,
    disc_status_enum,
    job_status_enum,
    rip_quality_enum,
    media_type_enum,
    encode_target_enum,
]

def upgrade() -> None:
    bind = op.get_bind()

    disc_type_enum.create(bind, checkfirst=True)
    disc_status_enum.create(bind, checkfirst=True)
    job_status_enum.create(bind, checkfirst=True)
    rip_quality_enum.create(bind, checkfirst=True)
    media_type_enum.create(bind, checkfirst=True)
    encode_target_enum.create(bind, checkfirst=True)

    op.create_table(
        "drives",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("device_path", sa.String, nullable=False),
        sa.Column("env", sa.String, nullable=False),
        sa.Column("drive_type", disc_type_enum, nullable=False),
        sa.Column("label", sa.String, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "catalog",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("mymovies_id", sa.String, nullable=True, unique=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("year", sa.Integer, nullable=True),
        sa.Column("media_type", media_type_enum, nullable=False, server_default="movie"),
        sa.Column("raw_metadata", sa.JSON, nullable=True),
        sa.Column("synced_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_catalog_mymovies_id", "catalog", ["mymovies_id"])

    op.create_table(
        "discs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("type", disc_type_enum, nullable=False),
        sa.Column("status", disc_status_enum, nullable=False, server_default="queued"),
        sa.Column("drive_id", sa.Integer, sa.ForeignKey("drives.id"), nullable=True),
        sa.Column("catalog_id", sa.Integer, sa.ForeignKey("catalog.id"), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("ripped_at", sa.DateTime, nullable=True),
        sa.Column("raw_path", sa.String, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("disc_fingerprint", sa.String, nullable=True),
        sa.Column("album_title", sa.String, nullable=True),
        sa.Column("album_artist", sa.String, nullable=True),
        sa.Column("needs_rerip", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_discs_disc_fingerprint", "discs", ["disc_fingerprint"])

    op.create_table(
        "cd_tracks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("disc_id", sa.Integer, sa.ForeignKey("discs.id"), nullable=False),
        sa.Column("track_number", sa.Integer, nullable=False),
        sa.Column("title", sa.String, nullable=True),
        sa.Column("artist", sa.String, nullable=True),
        sa.Column("duration_seconds", sa.Float, nullable=True),
        sa.Column("wav_filename", sa.String, nullable=True),
        sa.Column("rip_quality", rip_quality_enum, nullable=True),
        sa.Column("rip_log", sa.Text, nullable=True),
    )

    op.create_table(
        "lookup_candidates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("disc_id", sa.Integer, sa.ForeignKey("discs.id"), nullable=False),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("candidate_data", sa.JSON, nullable=False),
        sa.Column("selected", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "encode_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("target", encode_target_enum, nullable=False),
        sa.Column("format", sa.String, nullable=False),
        sa.Column("params", sa.JSON, nullable=True),
        sa.Column("output_subfolder", sa.String, nullable=False),
    )

    op.create_table(
        "rip_jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("disc_id", sa.Integer, sa.ForeignKey("discs.id"), nullable=False),
        sa.Column("drive_id", sa.Integer, sa.ForeignKey("drives.id"), nullable=True),
        sa.Column("status", job_status_enum, nullable=False, server_default="queued"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("log", sa.Text, nullable=True),
    )

    op.create_table(
        "encode_jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("disc_id", sa.Integer, sa.ForeignKey("discs.id"), nullable=False),
        sa.Column("profile_id", sa.Integer, sa.ForeignKey("encode_profiles.id"), nullable=False),
        sa.Column("status", job_status_enum, nullable=False, server_default="queued"),
        sa.Column("source_file", sa.String, nullable=True),
        sa.Column("output_path", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("encode_jobs")
    op.drop_table("rip_jobs")
    op.drop_table("encode_profiles")
    op.drop_table("lookup_candidates")
    op.drop_table("cd_tracks")
    op.drop_index("ix_discs_disc_fingerprint", table_name="discs")
    op.drop_table("discs")
    op.drop_index("ix_catalog_mymovies_id", table_name="catalog")
    op.drop_table("catalog")
    op.drop_table("drives")

    bind = op.get_bind()
    encode_target_enum.drop(bind, checkfirst=True)
    media_type_enum.drop(bind, checkfirst=True)
    rip_quality_enum.drop(bind, checkfirst=True)
    job_status_enum.drop(bind, checkfirst=True)
    disc_status_enum.drop(bind, checkfirst=True)
    disc_type_enum.drop(bind, checkfirst=True)
