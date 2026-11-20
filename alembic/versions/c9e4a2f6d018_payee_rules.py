"""Pedro's payee rulings move into the rules table (PLAN step 9 D8)

The Multibanco entities, the payment texts that name a payee and the supermarket chains were lists in the code; they
become Pedro's person rules, with a `merchant_key` column so the merchants the entities made keep their keys
(`entity:27134`). They already ranked as his answers, so nothing a family member sees changes.

Revision ID: c9e4a2f6d018
Revises: b8d1f3a5c702
"""
import sqlalchemy as sa
from alembic import op

revision = "c9e4a2f6d018"
down_revision = "b8d1f3a5c702"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("rules") as batch:
        batch.add_column(sa.Column("merchant_key", sa.String(), nullable=True))
    from sqlmodel import Session

    from app.services.payment_entities import seed
    from app.services.taxonomy import ensure_taxonomy
    session = Session(bind=op.get_bind())
    ensure_taxonomy(session)
    seed(session)
    session.flush()


def downgrade() -> None:
    op.execute("DELETE FROM rules WHERE note = 'Pedro''s ruling, moved from the code into the rules (PLAN step 9 D8)'")
    with op.batch_alter_table("rules") as batch:
        batch.drop_column("merchant_key")
