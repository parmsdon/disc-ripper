"""set name_confirmed for imported discs

Revision ID: 0029_name_confirmed_imported
Revises: 0028_name_confirmed
Create Date: 2026-11-27

Imported ISOs never pass through a drive card, so their working title
(derived from the ISO filename) is already confirmed by definition.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0029_name_confirmed_imported"
down_revision: Union[str, None] = "0028_name_confirmed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE discs SET name_confirmed = TRUE WHERE imported = TRUE"
    )


def downgrade() -> None:
    # Cannot safely un-confirm: some imported discs may have been manually
    # confirmed by users since. Downgrade is a no-op.
    pass
