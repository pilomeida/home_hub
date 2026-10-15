"""documents.sensitivity_tier and records.sensitivity_tier (house_low|house_mid|house_high; NULL = not wired)

Revision ID: f7b3d5e9a182
Revises: e6a2c4d8f731
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7b3d5e9a182"
down_revision: Union[str, Sequence[str], None] = "e6a2c4d8f731"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as b:
        b.add_column(sa.Column("sensitivity_tier", sa.String(), nullable=True))
    with op.batch_alter_table("records") as b:
        b.add_column(sa.Column("sensitivity_tier", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("records") as b:
        b.drop_column("sensitivity_tier")
    with op.batch_alter_table("documents") as b:
        b.drop_column("sensitivity_tier")
