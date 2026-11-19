"""Which fields of a paper a person typed or changed, so "Read again" keeps them (PLAN step 5d)

Revision ID: a9c3e5f7b124
Revises: f8a2c4e6b913
"""
import sqlalchemy as sa
from alembic import op

revision = "a9c3e5f7b124"
down_revision = "f8a2c4e6b913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("person_fields_json", sa.String(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("person_fields_json")
