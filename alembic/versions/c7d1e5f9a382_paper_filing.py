"""PLAN step 12d: a filed paper goes by its new name (the old one kept), carries its tags and who it is about, and
lists its items (paper_items)

Revision ID: c7d1e5f9a382
Revises: b4c8e2a6d153
"""
import sqlalchemy as sa
from alembic import op

revision = "c7d1e5f9a382"
down_revision = "b4c8e2a6d153"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("original_filename", sa.String(), nullable=True))
        batch.add_column(sa.Column("tags_json", sa.String(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("who_subject_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_documents_who_subject_id_subjects", "subjects", ["who_subject_id"], ["id"])
        batch.create_index("ix_documents_who_subject_id", ["who_subject_id"])
    op.create_table(
        "paper_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("room", sa.String(), nullable=True),
        sa.Column("warranty_end", sa.String(), nullable=True),
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("subjects.id"), nullable=True),
    )
    op.create_index("ix_paper_items_document_id", "paper_items", ["document_id"])
    op.create_index("ix_paper_items_subject_id", "paper_items", ["subject_id"])


def downgrade() -> None:
    op.drop_index("ix_paper_items_subject_id", table_name="paper_items")
    op.drop_index("ix_paper_items_document_id", table_name="paper_items")
    op.drop_table("paper_items")
    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_who_subject_id")
        batch.drop_constraint("fk_documents_who_subject_id_subjects", type_="foreignkey")
        batch.drop_column("who_subject_id")
        batch.drop_column("tags_json")
        batch.drop_column("original_filename")
