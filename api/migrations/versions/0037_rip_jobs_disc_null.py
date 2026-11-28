"""rip_jobs.disc_id: nullable + ON DELETE SET NULL

Revision ID: 0037_rip_jobs_disc_null
Revises: 0036_rip_job_outcomes
Create Date: 2026-11-28

Deleting a disc must not erase its rip history.  Changing the FK from
CASCADE to SET NULL means rip_jobs survive disc deletion with disc_id=NULL
but drive_id, outcome, and timing data intact — Drive Health counts are
therefore not affected by disc deletes.
"""
from alembic import op
import sqlalchemy as sa

revision = "0037_rip_jobs_disc_null"
down_revision = "0036_rip_job_outcomes"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("rip_jobs", "disc_id", nullable=True)
    op.drop_constraint("rip_jobs_disc_id_fkey", "rip_jobs", type_="foreignkey")
    op.create_foreign_key(
        None, "rip_jobs", "discs", ["disc_id"], ["id"], ondelete="SET NULL"
    )


def downgrade():
    op.drop_constraint(None, "rip_jobs", type_="foreignkey")
    op.create_foreign_key(
        "rip_jobs_disc_id_fkey", "rip_jobs", "discs", ["disc_id"], ["id"]
    )
    # Rows with disc_id=NULL cannot satisfy NOT NULL; handle before downgrading.
    op.alter_column("rip_jobs", "disc_id", nullable=False)
