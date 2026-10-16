"""Health entries: check_notes, what the check of a reading against its paper could not settle

Revision ID: e5c9d2f16b37
Revises: d4a7b1c05e26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5c9d2f16b37"
down_revision: Union[str, Sequence[str], None] = "d4a7b1c05e26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("health_entries") as batch:
        batch.add_column(sa.Column("check_notes", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("health_entries") as batch:
        batch.drop_column("check_notes")
