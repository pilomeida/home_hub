"""tags on category nodes: cross-cutting labels for spending analysis

Revision ID: d4b8e2a6c931
Revises: c3a9d4e7f215
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlmodel import Session

revision: str = "d4b8e2a6c931"
down_revision: Union[str, Sequence[str], None] = "c3a9d4e7f215"
branch_labels = None
depends_on = None

STR = sqlmodel.sql.sqltypes.AutoString


def _fk(col, target):
    return sa.ForeignKeyConstraint([col], [target])


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", STR(), nullable=False),
        sa.Column("label", STR(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("tags") as b:
        b.create_index("ix_tags_name", ["name"], unique=True)

    op.create_table(
        "node_tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        _fk("node_id", "category_nodes.id"), _fk("tag_id", "tags.id"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("node_id", "tag_id", name="uq_node_tags_node_id_tag_id"),
    )
    with op.batch_alter_table("node_tags") as b:
        b.create_index("ix_node_tags_node_id", ["node_id"])
        b.create_index("ix_node_tags_tag_id", ["tag_id"])


def downgrade() -> None:
    op.drop_table("node_tags")
    op.drop_table("tags")
