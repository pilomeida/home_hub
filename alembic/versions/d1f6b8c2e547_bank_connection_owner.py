"""bank_connections.owner: whose login authorised the access (Pedro's, Rute's)

Revision ID: d1f6b8c2e547
Revises: c9e5a7b1d436
"""
import sqlalchemy as sa
from alembic import op

revision = "d1f6b8c2e547"
down_revision = "c9e5a7b1d436"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bank_connections") as b:
        b.add_column(sa.Column("owner", sa.String(), nullable=False, server_default="Pedro"))


def downgrade() -> None:
    with op.batch_alter_table("bank_connections") as b:
        b.drop_column("owner")
