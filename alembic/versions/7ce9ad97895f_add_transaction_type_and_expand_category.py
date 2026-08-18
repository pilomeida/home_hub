"""add transaction_type and expand category

Revision ID: 7ce9ad97895f
Revises: ebf9c2a43192
Create Date: 2026-08-18 10:57:16.515069

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '7ce9ad97895f'
down_revision: Union[str, Sequence[str], None] = 'ebf9c2a43192'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("transactions", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "transaction_type",
                sa.Enum("DEBIT", "CREDIT", "TRANSFER", name="transactiontype"),
                nullable=False,
                server_default="DEBIT",
            )
        )
        batch_op.alter_column(
            "category",
            existing_type=sa.Enum(
                "ELECTRICITY", "WATER", "GAS", "TELECOM", "INSURANCE",
                "SUBSCRIPTIONS", "GROCERIES", "HEALTH", "HOME", "OTHER",
                name="category",
            ),
            type_=sa.Enum(
                "ELECTRICITY", "WATER", "GAS", "TELECOM", "INSURANCE",
                "SUBSCRIPTIONS", "GROCERIES", "HEALTH", "HOME", "INCOME",
                "TRANSFER", "ATM_WITHDRAWAL", "RESTAURANTS", "SHOPPING",
                "OTHER_EXPENSE", "OTHER",
                name="category",
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("transactions", recreate="always") as batch_op:
        batch_op.drop_column("transaction_type")
        batch_op.alter_column(
            "category",
            existing_type=sa.Enum(
                "ELECTRICITY", "WATER", "GAS", "TELECOM", "INSURANCE",
                "SUBSCRIPTIONS", "GROCERIES", "HEALTH", "HOME", "INCOME",
                "TRANSFER", "ATM_WITHDRAWAL", "RESTAURANTS", "SHOPPING",
                "OTHER_EXPENSE", "OTHER",
                name="category",
            ),
            type_=sa.Enum(
                "ELECTRICITY", "WATER", "GAS", "TELECOM", "INSURANCE",
                "SUBSCRIPTIONS", "GROCERIES", "HEALTH", "HOME", "OTHER",
                name="category",
            ),
        )
