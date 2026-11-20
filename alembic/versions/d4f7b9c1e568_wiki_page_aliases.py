"""Wiki pages keep the other titles that lead to them (PLAN step 6c: a topic's Portuguese title once it is in English)

Revision ID: d4f7b9c1e568
Revises: c3e6a8b0d457
"""
import sqlalchemy as sa
from alembic import op

revision = "d4f7b9c1e568"
down_revision = "c3e6a8b0d457"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("wiki_pages", sa.Column("aliases_json", sa.String(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("wiki_pages") as batch:
        batch.drop_column("aliases_json")
