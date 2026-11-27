"""add cancelled value to discstatus and jobstatus enums

Revision ID: 0033_cancelled_status
Revises: 0032_fix_stuck_queued_discs
Create Date: 2026-11-27

Adds 'cancelled' to both Postgres enum types so the application can
distinguish a job that was stopped for external reasons (disc ejected,
user Cancel, disc swapped) from a genuine rip failure.
"""
from alembic import op


revision = "0033_cancelled_status"
down_revision = "0032_fix_stuck_queued_discs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE discstatus ADD VALUE IF NOT EXISTS 'cancelled'")
    op.execute("ALTER TYPE jobstatus  ADD VALUE IF NOT EXISTS 'cancelled'")


def downgrade() -> None:
    # Postgres does not support removing enum values; leave both types intact.
    pass
