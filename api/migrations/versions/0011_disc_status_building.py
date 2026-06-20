"""disc status: add 'building'

Revision ID: 0011_disc_status_building
Revises: 0010_ripjob_progress
Create Date: 2026-06-20

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0011_disc_status_building"
down_revision: Union[str, None] = "0010_ripjob_progress"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE can't run inside a transaction block at all
    # on Postgres < 12, and even on 12+ the new value can't be used in the
    # same transaction it was added in. autocommit_block() sidesteps both
    # concerns regardless of server version.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE discstatus ADD VALUE 'building' AFTER 'ripping'")


def downgrade() -> None:
    # Postgres has no "DROP VALUE" for enums - removing one requires
    # rebuilding the type from scratch. Not implemented here; downgrading
    # past this revision requires a manual migration if ever needed,
    # after first migrating any 'building' rows to another status.
    raise NotImplementedError(
        "Cannot remove an enum value in Postgres without rebuilding the "
        "type. Manually handle any 'building' rows first if downgrade is "
        "required."
    )
