"""records (hand-entered sources), claim notes, record-or-document claim sources

Revision ID: e7b3d1f4a6c8
Revises: c5f1a8e3d902
Create Date: 2026-09-24 00:00:03.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'e7b3d1f4a6c8'
down_revision: Union[str, Sequence[str], None] = 'c5f1a8e3d902'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_AUTO = sqlmodel.sql.sqltypes.AutoString
_DOMAIN = sa.Enum("FINANCIALS", "HOUSE", name="domain")


def upgrade() -> None:
    op.create_table(
        "records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("domain", _DOMAIN, nullable=False),
        sa.Column("category", _AUTO(), nullable=False),
        sa.Column("fields_json", _AUTO(), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=True),
        sa.Column("entered_by", _AUTO(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_records_domain", "records", ["domain"], unique=False)
    op.create_index("ix_records_category", "records", ["category"], unique=False)
    op.create_index("ix_records_document_id", "records", ["document_id"], unique=True)

    with op.batch_alter_table("wiki_claims", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("note", _AUTO(), nullable=True))

    with op.batch_alter_table("wiki_claim_sources", recreate="always") as batch_op:
        batch_op.alter_column("document_id", existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(sa.Column("record_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("withdrawn_at", sa.DateTime(), nullable=True))
        batch_op.create_foreign_key("fk_wiki_claim_sources_record_id_records_id", "records", ["record_id"], ["id"])
        batch_op.create_index("ix_wiki_claim_sources_record_id", ["record_id"], unique=False)
        batch_op.create_unique_constraint("uq_wiki_claim_sources_claim_record", ["claim_id", "record_id"])
        batch_op.create_check_constraint("ck_wiki_claim_sources_one_source", "(document_id IS NULL) <> (record_id IS NULL)")

    with op.batch_alter_table("wiki_log", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("record_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_wiki_log_record_id_records_id", "records", ["record_id"], ["id"])

    with op.batch_alter_table("todos", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("record_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_todos_record_id_records_id", "records", ["record_id"], ["id"])
        batch_op.create_index("ix_todos_record_id", ["record_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("todos", recreate="always") as batch_op:
        batch_op.drop_index("ix_todos_record_id")
        batch_op.drop_constraint("fk_todos_record_id_records_id", type_="foreignkey")
        batch_op.drop_column("record_id")
    with op.batch_alter_table("wiki_log", recreate="always") as batch_op:
        batch_op.drop_constraint("fk_wiki_log_record_id_records_id", type_="foreignkey")
        batch_op.drop_column("record_id")
    op.execute("DELETE FROM wiki_claim_sources WHERE record_id IS NOT NULL")
    with op.batch_alter_table("wiki_claim_sources", recreate="always") as batch_op:
        batch_op.drop_constraint("ck_wiki_claim_sources_one_source", type_="check")
        batch_op.drop_constraint("uq_wiki_claim_sources_claim_record", type_="unique")
        batch_op.drop_index("ix_wiki_claim_sources_record_id")
        batch_op.drop_constraint("fk_wiki_claim_sources_record_id_records_id", type_="foreignkey")
        batch_op.drop_column("withdrawn_at")
        batch_op.drop_column("record_id")
        batch_op.alter_column("document_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("wiki_claims", recreate="always") as batch_op:
        batch_op.drop_column("note")
    op.drop_index("ix_records_document_id", table_name="records")
    op.drop_index("ix_records_category", table_name="records")
    op.drop_index("ix_records_domain", table_name="records")
    op.drop_table("records")
