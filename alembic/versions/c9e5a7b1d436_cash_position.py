"""transactions.cash_sign and bank_account_links.balance_at: roll the cash position forward from the bank's balance

Revision ID: c9e5a7b1d436
Revises: b8d4f6a0c325
"""
import sqlalchemy as sa
from alembic import op

revision = "c9e5a7b1d436"
down_revision = "b8d4f6a0c325"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transactions") as b:
        b.add_column(sa.Column("cash_sign", sa.Integer(), nullable=True))
    with op.batch_alter_table("bank_account_links") as b:
        b.add_column(sa.Column("balance_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bank_account_links") as b:
        b.drop_column("balance_at")
    with op.batch_alter_table("transactions") as b:
        b.drop_column("cash_sign")
