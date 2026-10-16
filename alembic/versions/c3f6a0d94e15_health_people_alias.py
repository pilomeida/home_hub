"""Health people: merged_into_id, so a merge sticks (the old spelling stays as an alias)

Revision ID: c3f6a0d94e15
Revises: b2e5f9c83d04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3f6a0d94e15"
down_revision: Union[str, Sequence[str], None] = "b2e5f9c83d04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("health_people") as batch:
        batch.add_column(sa.Column("merged_into_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_health_people_merged_into", "health_people", ["merged_into_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("health_people") as batch:
        batch.drop_constraint("fk_health_people_merged_into", type_="foreignkey")
        batch.drop_column("merged_into_id")
