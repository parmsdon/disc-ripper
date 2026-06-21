"""disc status: add 'identifying'

Revision ID: 0012_disc_status_identifying
Revises: 0011_disc_status_building
Create Date: 2026-06-21

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0012_disc_status_identifying"
down_revision: Union[str, None] = "0011_disc_status_building"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE can't run inside a transaction block at all
    # on Postgres < 12, and even on 12+ the new value can't be used in the
    # same transaction it was added in. autocommit_block() sidesteps both
    # concerns regardless of server version.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE discstatus ADD VALUE 'identifying' AFTER 'building'")


def downgrade() -> None:
    # Postgres has no "DROP VALUE" for enums - removing one requires
    # rebuilding the type from scratch. Not implemented here; downgrading
    # past this revision requires a manual migration if ever needed,
    # after first migrating any 'identifying' rows to another status.
    raise NotImplementedError(
        "Cannot remove an enum value in Postgres without rebuilding the "
        "type. Manually handle any 'identifying' rows first if downgrade is "
        "required."
    )
