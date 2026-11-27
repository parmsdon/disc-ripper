"""Per-job outcome columns on rip_jobs

Revision ID: 0036_rip_job_outcomes
Revises: 0035_sync_state_heartbeat
Create Date: 2026-11-27

Adds outcome, dvdbackup_outcome, makemkv_outcome, dvdbackup_seconds,
makemkv_seconds, iso_size_bytes to rip_jobs.  All nullable — a backfill
script populates existing rows as far as the log-event record allows;
new jobs are populated by the ripper service at completion time.
"""
from alembic import op
import sqlalchemy as sa

revision = "0036_rip_job_outcomes"
down_revision = "0035_sync_state_heartbeat"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("rip_jobs", sa.Column("outcome",           sa.String, nullable=True))
    op.add_column("rip_jobs", sa.Column("dvdbackup_outcome", sa.String, nullable=True))
    op.add_column("rip_jobs", sa.Column("makemkv_outcome",   sa.String, nullable=True))
    op.add_column("rip_jobs", sa.Column("dvdbackup_seconds", sa.Float,  nullable=True))
    op.add_column("rip_jobs", sa.Column("makemkv_seconds",   sa.Float,  nullable=True))
    op.add_column("rip_jobs", sa.Column("iso_size_bytes",    sa.BigInteger, nullable=True))


def downgrade():
    op.drop_column("rip_jobs", "iso_size_bytes")
    op.drop_column("rip_jobs", "makemkv_seconds")
    op.drop_column("rip_jobs", "dvdbackup_seconds")
    op.drop_column("rip_jobs", "makemkv_outcome")
    op.drop_column("rip_jobs", "dvdbackup_outcome")
    op.drop_column("rip_jobs", "outcome")
