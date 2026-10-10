"""merchants.default_credit_category_id, and the new leaves Psi expenses > Session room rental and Cash paid into the account

Revision ID: d5f1b3c9e826
Revises: c4e8a2b7d913
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlmodel import Session

revision: str = "d5f1b3c9e826"
down_revision: Union[str, Sequence[str], None] = "c4e8a2b7d913"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("merchants") as b:
        b.add_column(sa.Column("default_credit_category_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_merchants_default_credit_category_id", "category_nodes", ["default_credit_category_id"], ["id"])
    from app.services.taxonomy import ensure_taxonomy
    ensure_taxonomy(Session(bind=op.get_bind()))  # creates the two new leaves


def downgrade() -> None:
    with op.batch_alter_table("merchants") as b:
        b.drop_constraint("fk_merchants_default_credit_category_id", type_="foreignkey")
        b.drop_column("default_credit_category_id")
