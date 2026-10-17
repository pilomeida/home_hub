"""Inbox skips: files that arrived but were not added (exact duplicate, decorative image), with the reason

Revision ID: f6d0e3a27c48
Revises: e5c9d2f16b37
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6d0e3a27c48"
down_revision: Union[str, Sequence[str], None] = "e5c9d2f16b37"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbox_skips",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=True),
        sa.Column("external_ref", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_inbox_skips_reason", "inbox_skips", ["reason"])
    op.create_index("ix_inbox_skips_created_at", "inbox_skips", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_inbox_skips_created_at", table_name="inbox_skips")
    op.drop_index("ix_inbox_skips_reason", table_name="inbox_skips")
    op.drop_table("inbox_skips")
