"""transactions: source_page + review fields; statement_checks (does a statement add up?)

Revision ID: e5b9d7f3a826
Revises: d4a8c6e2b715
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5b9d7f3a826"
down_revision: Union[str, Sequence[str], None] = "d4a8c6e2b715"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transactions") as b:
        b.add_column(sa.Column("source_page", sa.Integer(), nullable=True))
        b.add_column(sa.Column("review_status", sa.String(), nullable=True))
        b.add_column(sa.Column("review_reason", sa.String(), nullable=True))
        b.add_column(sa.Column("review_original_json", sa.String(), nullable=True))
        b.add_column(sa.Column("checked_by", sa.String(), nullable=True))
        b.add_column(sa.Column("checked_at", sa.DateTime(), nullable=True))
        b.create_index("ix_transactions_review_status", ["review_status"])
    op.create_table(
        "statement_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("opening_balance", sa.Float(), nullable=True),
        sa.Column("closing_balance", sa.Float(), nullable=True),
        sa.Column("lines_total", sa.Float(), nullable=True),
        sa.Column("difference", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="unverified"),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_by", sa.String(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_statement_checks_document_id", "statement_checks", ["document_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_statement_checks_document_id", table_name="statement_checks")
    op.drop_table("statement_checks")
    with op.batch_alter_table("transactions") as b:
        b.drop_index("ix_transactions_review_status")
        for col in ("checked_at", "checked_by", "review_original_json", "review_reason", "review_status", "source_page"):
            b.drop_column(col)
