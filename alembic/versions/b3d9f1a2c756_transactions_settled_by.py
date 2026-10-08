"""transactions.settled_by_id: a document-made row points at the bank row that paid it

Revision ID: b3d9f1a2c756
Revises: a8c3e5f1d902
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3d9f1a2c756"
down_revision: Union[str, Sequence[str], None] = "a8c3e5f1d902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transactions") as b:
        b.add_column(sa.Column("settled_by_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_transactions_settled_by_id_transactions", "transactions", ["settled_by_id"], ["id"])
        b.create_index("ix_transactions_settled_by_id", ["settled_by_id"])


def downgrade() -> None:
    with op.batch_alter_table("transactions") as b:
        b.drop_index("ix_transactions_settled_by_id")
        b.drop_constraint("fk_transactions_settled_by_id_transactions", type_="foreignkey")
        b.drop_column("settled_by_id")
