"""merchants.merged_into_id (a merged merchant stays as an alias of the survivor) and transactions.issue_date

Revision ID: c4e8a2b7d913
Revises: b3d9f1a2c756
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a2b7d913"
down_revision: Union[str, Sequence[str], None] = "b3d9f1a2c756"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("merchants") as b:
        b.add_column(sa.Column("merged_into_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_merchants_merged_into_id_merchants", "merchants", ["merged_into_id"], ["id"])
        b.create_index("ix_merchants_merged_into_id", ["merged_into_id"])
    with op.batch_alter_table("transactions") as b:
        b.add_column(sa.Column("issue_date", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("transactions") as b:
        b.drop_column("issue_date")
    with op.batch_alter_table("merchants") as b:
        b.drop_index("ix_merchants_merged_into_id")
        b.drop_constraint("fk_merchants_merged_into_id_merchants", type_="foreignkey")
        b.drop_column("merged_into_id")
