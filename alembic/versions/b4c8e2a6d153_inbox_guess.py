"""PLAN step 12b: the whole guess kept on the Inbox item (who, items with rooms, tags, date to act on, filed name)

Revision ID: b4c8e2a6d153
Revises: a12f3c5e7b90
"""
import sqlalchemy as sa
from alembic import op

revision = "b4c8e2a6d153"
down_revision = "a12f3c5e7b90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("inbox_items") as batch:
        batch.add_column(sa.Column("guess_json", sa.String(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("inbox_items") as batch:
        batch.drop_column("guess_json")
