"""baseline_positions: the opening position on the baseline day

Revision ID: a7c3e5f9b214
Revises: e6a2c4d8f731
"""
import sqlalchemy as sa
from alembic import op

revision = "a7c3e5f9b214"
down_revision = "e6a2c4d8f731"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "baseline_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False, index=True),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=True),
        sa.Column("debt_id", sa.Integer(), sa.ForeignKey("debts.id"), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("baseline_positions")
