"""The "See it" link of an AI answer in the queue pointed at /transactions, a page that does not exist; point the stored
items at /financials/transactions (PLAN step 10e, found 2026-11-20)

Revision ID: b1d4f6a8c235
Revises: a9c3e5f7b124
"""
from alembic import op

revision = "b1d4f6a8c235"
down_revision = "a9c3e5f7b124"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE review_items SET evidence_url = '/financials' || evidence_url "
               "WHERE kind = 'ai_rule' AND evidence_url LIKE '/transactions?%'")


def downgrade() -> None:
    pass                                    # the old link led nowhere; nothing to restore
