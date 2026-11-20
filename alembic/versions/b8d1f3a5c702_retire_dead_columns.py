"""Retire dead tables and columns (PLAN step 9, D1): wiki_changes (pre-claims fact history; one row from 2026-08, its
facts are in the claims), documents.doc_type (replaced by the paper kind; every row carried the same value there),
health_entries.amount_billed / amount_reimbursed / amount_owed (never filled since bills left Health).

SQLite 3.35+ drops a plain column in place (no index, key or constraint on these), so no table is rebuilt.
Downgrade adds them back empty.

Revision ID: b8d1f3a5c702
Revises: a7c0e2f4b891
"""
import sqlalchemy as sa
from alembic import op

revision = "b8d1f3a5c702"
down_revision = "a7c0e2f4b891"
branch_labels = None
depends_on = None

_HEALTH_AMOUNTS = ("amount_billed", "amount_reimbursed", "amount_owed")


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("wiki_changes"):
        op.drop_table("wiki_changes")
    if "doc_type" in _columns("documents"):
        op.drop_column("documents", "doc_type")
    have = _columns("health_entries")
    for name in _HEALTH_AMOUNTS:
        if name in have:
            op.drop_column("health_entries", name)


def downgrade() -> None:
    for name in _HEALTH_AMOUNTS:
        op.add_column("health_entries", sa.Column(name, sa.Float(), nullable=True))
    op.add_column("documents", sa.Column("doc_type", sa.String(), nullable=True))
    op.create_table("wiki_changes", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("wiki_page_id", sa.Integer(), sa.ForeignKey("wiki_pages.id"), nullable=False, index=True),
                    sa.Column("fact_key", sa.String(), nullable=False), sa.Column("old_value", sa.String()),
                    sa.Column("new_value", sa.String(), nullable=False),
                    sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id")),
                    sa.Column("changed_at", sa.DateTime(), nullable=False))
