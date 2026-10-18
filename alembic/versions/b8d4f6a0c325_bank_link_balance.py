"""bank_account_links.balance, balance_as_of: the balance the bank reports

Revision ID: b8d4f6a0c325
Revises: a7c3e5f9b214
"""
import sqlalchemy as sa
from alembic import op

revision = "b8d4f6a0c325"
down_revision = "a7c3e5f9b214"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bank_account_links") as b:
        b.add_column(sa.Column("balance", sa.Float(), nullable=True))
        b.add_column(sa.Column("balance_as_of", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bank_account_links") as b:
        b.drop_column("balance_as_of")
        b.drop_column("balance")
