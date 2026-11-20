"""Wiki pages point at the subject they are about (PLAN step 6a)

Revision ID: c3e6a8b0d457
Revises: b1d4f6a8c235
"""
import sqlalchemy as sa
from alembic import op

revision = "c3e6a8b0d457"
down_revision = "b1d4f6a8c235"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("wiki_pages") as b:
        b.add_column(sa.Column("subject_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_wiki_pages_subject_id", "subjects", ["subject_id"], ["id"])
    op.create_index("ix_wiki_pages_subject_id", "wiki_pages", ["subject_id"])


def downgrade() -> None:
    op.drop_index("ix_wiki_pages_subject_id", "wiki_pages")
    with op.batch_alter_table("wiki_pages") as b:
        b.drop_constraint("fk_wiki_pages_subject_id", type_="foreignkey")
        b.drop_column("subject_id")
