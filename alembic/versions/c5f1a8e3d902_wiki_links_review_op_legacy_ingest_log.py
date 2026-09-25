"""wiki links, REVIEW log operation, legacy ingest log entries

Revision ID: c5f1a8e3d902
Revises: 9c4d2a7e5b18
Create Date: 2026-09-24 00:00:02.000000

Adds wiki_links (directed page-to-page cross-links), adds REVIEW to the
wiki_log operation enum (Plan B's Inbox approve/discard), and records one
INGEST log entry for every finalized, processed document that predates the
knowledge layer, so "never ingested" (Plan C's lint) means what it says.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5f1a8e3d902'
down_revision: Union[str, Sequence[str], None] = '9c4d2a7e5b18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_OP = sa.Enum("INGEST", "EDIT", "QUERY", "LINT", "MIGRATION", name="wikioperation")
_NEW_OP = sa.Enum("INGEST", "EDIT", "QUERY", "LINT", "REVIEW", "MIGRATION", name="wikioperation")
_LEGACY_PREFIX = "Ingested before the knowledge layer existed: "


def upgrade() -> None:
    op.create_table(
        "wiki_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("from_page_id", sa.Integer(), sa.ForeignKey("wiki_pages.id"), nullable=False),
        sa.Column("to_page_id", sa.Integer(), sa.ForeignKey("wiki_pages.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("from_page_id", "to_page_id", name="uq_wiki_links_from_to"),
    )
    op.create_index("ix_wiki_links_from_page_id", "wiki_links", ["from_page_id"], unique=False)
    op.create_index("ix_wiki_links_to_page_id", "wiki_links", ["to_page_id"], unique=False)

    with op.batch_alter_table("wiki_log", recreate="always") as batch_op:
        batch_op.alter_column("operation", existing_type=_OLD_OP, type_=_NEW_OP, existing_nullable=False)

    op.execute(
        "INSERT INTO wiki_log (occurred_at, operation, description, document_id, page_ids_json) "
        f"SELECT d.created_at, 'INGEST', '{_LEGACY_PREFIX}' || d.filename, d.id, '[]' "
        "FROM documents d WHERE d.domain IS NOT NULL AND d.status = 'PROCESSED' "
        "AND NOT EXISTS (SELECT 1 FROM wiki_log l WHERE l.document_id = d.id AND l.operation = 'INGEST')"
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM wiki_log WHERE description LIKE '{_LEGACY_PREFIX}%'")
    op.execute("DELETE FROM wiki_log WHERE operation = 'REVIEW'")
    with op.batch_alter_table("wiki_log", recreate="always") as batch_op:
        batch_op.alter_column("operation", existing_type=_NEW_OP, type_=_OLD_OP, existing_nullable=False)
    op.drop_index("ix_wiki_links_to_page_id", table_name="wiki_links")
    op.drop_index("ix_wiki_links_from_page_id", table_name="wiki_links")
    op.drop_table("wiki_links")
