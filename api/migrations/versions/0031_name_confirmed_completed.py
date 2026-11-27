"""set name_confirmed for all completed discs

Revision ID: 0031_name_confirmed_completed
Revises: 0030_drives_uq_device_env
Create Date: 2026-11-27

Before this change, the name-lock feature did not exist: discs could be
ripped without ever confirming the name. Set name_confirmed = TRUE for
every disc whose rip has already completed so the encode gate never
blocks them. Do not rename any existing ISOs (the rename only runs at
the moment of a fresh lock).

Affected: any disc with status outside the in-flight set
(queued / ripping / building) that is still unconfirmed.

Dev count before migration: 2 (both status=ripped).
Prod count query:
  SELECT COUNT(*) FROM discs
  WHERE status NOT IN ('queued','ripping','building')
  AND name_confirmed = FALSE;
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0031_name_confirmed_completed"
down_revision: Union[str, None] = "0030_drives_uq_device_env"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        UPDATE discs
        SET name_confirmed = TRUE
        WHERE status NOT IN ('queued'::discstatus, 'ripping'::discstatus, 'building'::discstatus)
        AND name_confirmed = FALSE
    """)


def downgrade() -> None:
    pass
