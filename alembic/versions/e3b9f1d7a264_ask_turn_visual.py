"""The answer card's one visual, kept with the answer (PLAN step 10g, slice 2)

Revision ID: e3b9f1d7a264
Revises: d1a7e3c5f920
"""
import sqlalchemy as sa
from alembic import op

revision = "e3b9f1d7a264"
down_revision = "d1a7e3c5f920"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ask_turns") as batch:
        batch.add_column(sa.Column("visual_json", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ask_turns") as batch:
        batch.drop_column("visual_json")
