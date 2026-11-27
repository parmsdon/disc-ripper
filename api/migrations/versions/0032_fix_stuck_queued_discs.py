"""fix discs stuck in queued with no queued rip job

Revision ID: 0032_fix_stuck_queued_discs
Revises: 0031_name_confirmed_completed
Create Date: 2026-11-27

A disc can get stuck in status='queued' with no corresponding queued rip
job if the disc was ejected just as the ripper created the job (the new
safety rules introduced in this session will prevent future occurrences).
On dev, disc #512 is in this state.  Reset such discs to 'error' with
needs_rerip=TRUE so they are re-queued correctly when re-inserted.
"""
from alembic import op


revision = "0032_fix_stuck_queued_discs"
down_revision = "0031_name_confirmed_completed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE discs
        SET    status     = 'error',
               needs_rerip = TRUE
        WHERE  status = 'queued'
          AND  id NOT IN (
                   SELECT disc_id
                   FROM   rip_jobs
                   WHERE  status IN ('queued', 'running')
               )
    """)


def downgrade() -> None:
    pass
