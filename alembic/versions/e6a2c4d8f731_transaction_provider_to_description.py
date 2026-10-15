"""rename transactions.provider to transactions.description

The bank statement calls this column "Descrição"; the app now does too.

Revision ID: e6a2c4d8f731
Revises: d5f1b3c9e826
"""
import sqlalchemy as sa
from alembic import op

revision = "e6a2c4d8f731"
down_revision = "d5f1b3c9e826"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transactions") as b:
        b.alter_column("provider", new_column_name="description", existing_type=sa.String(), existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("transactions") as b:
        b.alter_column("description", new_column_name="provider", existing_type=sa.String(), existing_nullable=False)
