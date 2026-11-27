"""placeholder — no schema changes (rev reserved for safety-rules commit)

Revision ID: 0032_fix_stuck_queued_discs
Revises: 0031_name_confirmed_completed
Create Date: 2026-11-27

Schema anchor for the rip-job safety rules introduced in the same commit.
No DDL or DML: the dev-only data fix was applied directly, not via
migration (disc ids differ between environments).
"""
from alembic import op  # noqa: F401 — keep import so Alembic env.py loads cleanly


revision = "0032_fix_stuck_queued_discs"
down_revision = "0031_name_confirmed_completed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
