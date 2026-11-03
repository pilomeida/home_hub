"""A commitment sits on the spending tree, like the lines that pay it (work-list B3)

The old flat-list column `commitments.category` stays, unused. No commitment exists yet (2026-11-03), so nothing
is moved.

Revision ID: c9e2f4a6b815
Revises: a8d4c1e7f263
"""
import sqlalchemy as sa
from alembic import op

revision = "c9e2f4a6b815"
down_revision = "a8d4c1e7f263"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("commitments") as b:
        b.add_column(sa.Column("category_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_commitments_category_id", "category_nodes", ["category_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("commitments") as b:
        b.drop_constraint("fk_commitments_category_id", type_="foreignkey")
        b.drop_column("category_id")
