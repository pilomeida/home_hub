"""budgets table

Revision ID: b7d2f5a81c34
Revises: a1c4e7b92d10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2f5a81c34"
down_revision: Union[str, Sequence[str], None] = "a1c4e7b92d10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("expected_month", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["category_nodes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("node_id", "year", name="uq_budgets_node_year"),
    )
    with op.batch_alter_table("budgets") as b:
        b.create_index("ix_budgets_node_id", ["node_id"])
        b.create_index("ix_budgets_year", ["year"])


def downgrade() -> None:
    with op.batch_alter_table("budgets") as b:
        b.drop_index("ix_budgets_year")
        b.drop_index("ix_budgets_node_id")
    op.drop_table("budgets")
