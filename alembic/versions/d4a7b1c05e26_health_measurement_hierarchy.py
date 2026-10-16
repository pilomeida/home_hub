"""Health measurements: where a result sits on the paper (group path, order, reference as printed, earlier results)

Revision ID: d4a7b1c05e26
Revises: c3f6a0d94e15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4a7b1c05e26"
down_revision: Union[str, Sequence[str], None] = "c3f6a0d94e15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("health_measurements") as batch:
        batch.add_column(sa.Column("group_path", sa.String(), nullable=False, server_default=""))
        batch.add_column(sa.Column("position", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("reference_text", sa.String(), nullable=True))
        batch.add_column(sa.Column("method", sa.String(), nullable=True))
        batch.add_column(sa.Column("is_previous", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("health_measurements") as batch:
        batch.drop_column("is_previous")
        batch.drop_column("method")
        batch.drop_column("reference_text")
        batch.drop_column("position")
        batch.drop_column("group_path")
