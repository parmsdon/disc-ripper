"""discs: add rip_quality and rip_attempt_count

Revision ID: 0013_disc_rip_quality
Revises: 0012_disc_status_identifying
Create Date: 2026-06-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0013_disc_rip_quality"
down_revision: Union[str, None] = "0012_disc_status_identifying"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("discs", sa.Column("rip_quality", sa.String, nullable=True))
    op.add_column(
        "discs",
        sa.Column("rip_attempt_count", sa.Integer, nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("discs", "rip_attempt_count")
    op.drop_column("discs", "rip_quality")
