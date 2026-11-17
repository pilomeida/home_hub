"""Wiki facts say when they were true (Graphiti's valid time), apart from when the Hub learned them

Three columns on `wiki_claims`: valid_from, valid_to (dates, NULL = not known / still true) and was_correction
(a claim the same paper corrected: it was never true as stated). Nothing is filled here:
scripts/date_wiki_facts.py dates the facts already on file from their papers.

Revision ID: a3c7e1f9b250
Revises: f7b3d9e1a524
"""
import sqlalchemy as sa
from alembic import op

revision = "a3c7e1f9b250"
down_revision = "f7b3d9e1a524"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("wiki_claims") as batch:
        batch.add_column(sa.Column("valid_from", sa.Date(), nullable=True))
        batch.add_column(sa.Column("valid_to", sa.Date(), nullable=True))
        batch.add_column(sa.Column("was_correction", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("wiki_claims") as batch:
        batch.drop_column("was_correction")
        batch.drop_column("valid_to")
        batch.drop_column("valid_from")
