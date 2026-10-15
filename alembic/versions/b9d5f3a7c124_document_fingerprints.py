"""document_fingerprints (rules that recognise known recurring documents without AI)

Revision ID: b9d5f3a7c124
Revises: a8c4e2f6b913
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9d5f3a7c124"
down_revision: Union[str, Sequence[str], None] = "a8c4e2f6b913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_fingerprints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("pattern", sa.String(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_hit_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_document_fingerprints_kind", "document_fingerprints", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_document_fingerprints_kind", table_name="document_fingerprints")
    op.drop_table("document_fingerprints")
