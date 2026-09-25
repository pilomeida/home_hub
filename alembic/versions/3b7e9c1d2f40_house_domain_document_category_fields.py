"""house domain, document category + fields, todo document link

Revision ID: 3b7e9c1d2f40
Revises: 718ee63973b8
Create Date: 2026-09-24 00:00:00.000000

Adds HOUSE to the domain enum on documents/todos/wiki_pages; adds
documents.category (the generic per-domain category; Financials' doc_type
values are copied into it) and documents.fields_json (per-domain metadata);
adds todos.document_id (a to-do generated from a document, e.g. a warranty
renewal reminder). Additive only: doc_type is kept, merely no longer used.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '3b7e9c1d2f40'
down_revision: Union[str, Sequence[str], None] = '718ee63973b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_DOMAIN = sa.Enum("FINANCIALS", name="domain")
_NEW_DOMAIN = sa.Enum("FINANCIALS", "HOUSE", name="domain")


def upgrade() -> None:
    with op.batch_alter_table("documents", recreate="always") as batch_op:
        batch_op.alter_column("domain", existing_type=_OLD_DOMAIN, type_=_NEW_DOMAIN, existing_nullable=True)
        batch_op.add_column(sa.Column("category", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("fields_json", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.create_index("ix_documents_category", ["category"], unique=False)
    with op.batch_alter_table("todos", recreate="always") as batch_op:
        batch_op.alter_column("domain", existing_type=_OLD_DOMAIN, type_=_NEW_DOMAIN, existing_nullable=True)
        batch_op.add_column(sa.Column("document_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_todos_document_id_documents_id", "documents", ["document_id"], ["id"])
        batch_op.create_index("ix_todos_document_id", ["document_id"], unique=False)
    with op.batch_alter_table("wiki_pages", recreate="always") as batch_op:
        batch_op.alter_column("domain", existing_type=_OLD_DOMAIN, type_=_NEW_DOMAIN, existing_nullable=True)

    op.execute("UPDATE documents SET fields_json = '{}' WHERE fields_json IS NULL")
    # Financials' doc_type IS its category. Rows with NULL doc_type stay NULL:
    # dedup treats NULL as "not a statement", exactly as it treated NULL doc_type.
    op.execute("UPDATE documents SET category = doc_type WHERE category IS NULL AND doc_type IS NOT NULL")


def downgrade() -> None:
    with op.batch_alter_table("wiki_pages", recreate="always") as batch_op:
        batch_op.alter_column("domain", existing_type=_NEW_DOMAIN, type_=_OLD_DOMAIN, existing_nullable=True)
    with op.batch_alter_table("todos", recreate="always") as batch_op:
        batch_op.drop_index("ix_todos_document_id")
        batch_op.drop_constraint("fk_todos_document_id_documents_id", type_="foreignkey")
        batch_op.drop_column("document_id")
        batch_op.alter_column("domain", existing_type=_NEW_DOMAIN, type_=_OLD_DOMAIN, existing_nullable=True)
    with op.batch_alter_table("documents", recreate="always") as batch_op:
        batch_op.drop_index("ix_documents_category")
        batch_op.drop_column("fields_json")
        batch_op.drop_column("category")
        batch_op.alter_column("domain", existing_type=_NEW_DOMAIN, type_=_OLD_DOMAIN, existing_nullable=True)