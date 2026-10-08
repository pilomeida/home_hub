"""provider rules and the per-entry (by_provider) merchant flag

Revision ID: f2d7b9e4a183
Revises: e9c1a7d3b542
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "f2d7b9e4a183"
down_revision: Union[str, Sequence[str], None] = "e9c1a7d3b542"
branch_labels = None
depends_on = None

STR = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    op.create_table(
        "provider_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", STR(), nullable=False),
        sa.Column("example", STR(), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["category_nodes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("provider_rules") as b:
        b.create_index("ix_provider_rules_key", ["key"], unique=True)
        b.create_index("ix_provider_rules_node_id", ["node_id"])
    with op.batch_alter_table("merchants") as b:
        b.add_column(sa.Column("by_provider", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("merchants") as b:
        b.drop_column("by_provider")
    op.drop_table("provider_rules")
