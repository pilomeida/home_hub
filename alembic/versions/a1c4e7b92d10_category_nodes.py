"""category nodes + category_id on transactions/merchants

Revision ID: a1c4e7b92d10
Revises: 6d1e9c597432
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlmodel import Session

revision: str = "a1c4e7b92d10"
down_revision: Union[str, Sequence[str], None] = "6d1e9c597432"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "category_nodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("slug", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("cadence", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["category_nodes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("category_nodes") as b:
        b.create_index("ix_category_nodes_slug", ["slug"], unique=True)
        b.create_index("ix_category_nodes_parent_id", ["parent_id"])
    with op.batch_alter_table("transactions") as b:
        b.add_column(sa.Column("category_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_transactions_category_id", "category_nodes", ["category_id"], ["id"])
        b.create_index("ix_transactions_category_id", ["category_id"])
    with op.batch_alter_table("merchants") as b:
        b.add_column(sa.Column("default_category_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_merchants_default_category_id", "category_nodes", ["default_category_id"], ["id"])

    from app.services.taxonomy import ensure_taxonomy
    ensure_taxonomy(Session(bind=op.get_bind()))


def downgrade() -> None:
    with op.batch_alter_table("merchants") as b:
        b.drop_constraint("fk_merchants_default_category_id", type_="foreignkey")
        b.drop_column("default_category_id")
    with op.batch_alter_table("transactions") as b:
        b.drop_index("ix_transactions_category_id")
        b.drop_constraint("fk_transactions_category_id", type_="foreignkey")
        b.drop_column("category_id")
    op.drop_table("category_nodes")
