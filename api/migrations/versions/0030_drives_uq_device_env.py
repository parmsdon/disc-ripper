"""add unique constraint drives(device_path, env)

Revision ID: 0030_drives_uq_device_env
Revises: 0029_name_confirmed_imported
Create Date: 2026-11-27

Before adding the constraint, repoint any FK references that point at a
duplicate drive row (keeping the lowest id), then delete the extras.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0030_drives_uq_device_env"
down_revision: Union[str, None] = "0029_name_confirmed_imported"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Repoint FK references from duplicate drive rows to the surviving (min) id.
    op.execute("""
        UPDATE discs
        SET drive_id = keep.id
        FROM (
            SELECT device_path, env, MIN(id) AS id
            FROM drives
            GROUP BY device_path, env
        ) keep
        JOIN drives dup
          ON dup.device_path = keep.device_path
         AND dup.env = keep.env
         AND dup.id <> keep.id
        WHERE discs.drive_id = dup.id
    """)
    op.execute("""
        UPDATE rip_jobs
        SET drive_id = keep.id
        FROM (
            SELECT device_path, env, MIN(id) AS id
            FROM drives
            GROUP BY device_path, env
        ) keep
        JOIN drives dup
          ON dup.device_path = keep.device_path
         AND dup.env = keep.env
         AND dup.id <> keep.id
        WHERE rip_jobs.drive_id = dup.id
    """)

    # Delete duplicate rows (non-min ids).
    op.execute("""
        DELETE FROM drives
        WHERE id IN (
            SELECT dup.id
            FROM drives dup
            JOIN (
                SELECT device_path, env, MIN(id) AS id
                FROM drives
                GROUP BY device_path, env
                HAVING COUNT(*) > 1
            ) keep
              ON keep.device_path = dup.device_path
             AND keep.env = dup.env
             AND keep.id <> dup.id
        )
    """)

    op.create_unique_constraint(
        "uq_drives_device_path_env", "drives", ["device_path", "env"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_drives_device_path_env", "drives", type_="unique")
