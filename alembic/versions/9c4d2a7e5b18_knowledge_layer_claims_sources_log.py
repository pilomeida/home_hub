"""knowledge layer: wiki page types, claims, claim sources, log

Revision ID: 9c4d2a7e5b18
Revises: 3b7e9c1d2f40
Create Date: 2026-09-24 00:00:01.000000

Evolves the wiki into a claim-based knowledge layer. Existing pages become
'topic' pages; each existing fact becomes an ACTIVE claim, linked to the
document of the page's most recent WikiChange when one exists. wiki_changes
is left untouched (read-only history).
"""
import json
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '9c4d2a7e5b18'
down_revision: Union[str, Sequence[str], None] = '3b7e9c1d2f40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_AUTO = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    with op.batch_alter_table("wiki_pages", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("page_type", _AUTO(), nullable=True))
        batch_op.add_column(sa.Column("entity_key", _AUTO(), nullable=True))
        batch_op.add_column(sa.Column("summary", _AUTO(), nullable=True))
        batch_op.create_index("ix_wiki_pages_page_type", ["page_type"], unique=False)
        batch_op.create_unique_constraint("uq_wiki_pages_page_type_entity_key", ["page_type", "entity_key"])
    op.execute("UPDATE wiki_pages SET page_type = 'topic' WHERE page_type IS NULL")

    op.create_table(
        "wiki_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("page_id", sa.Integer(), sa.ForeignKey("wiki_pages.id"), nullable=False),
        sa.Column("key", _AUTO(), nullable=False),
        sa.Column("label", _AUTO(), nullable=True),
        sa.Column("value", _AUTO(), nullable=False),
        sa.Column("status", sa.Enum("ACTIVE", "SUPERSEDED", name="claimstatus"), nullable=False),
        sa.Column("superseded_by_claim_id", sa.Integer(), sa.ForeignKey("wiki_claims.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_wiki_claims_page_id", "wiki_claims", ["page_id"], unique=False)

    op.create_table(
        "wiki_claim_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("claim_id", sa.Integer(), sa.ForeignKey("wiki_claims.id"), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("claim_id", "document_id", name="uq_wiki_claim_sources_claim_document"),
    )
    op.create_index("ix_wiki_claim_sources_claim_id", "wiki_claim_sources", ["claim_id"], unique=False)
    op.create_index("ix_wiki_claim_sources_document_id", "wiki_claim_sources", ["document_id"], unique=False)

    op.create_table(
        "wiki_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("operation", sa.Enum("INGEST", "EDIT", "QUERY", "LINT", "MIGRATION", name="wikioperation"), nullable=False),
        sa.Column("description", _AUTO(), nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=True),
        sa.Column("page_ids_json", _AUTO(), nullable=False),
    )
    op.create_index("ix_wiki_log_occurred_at", "wiki_log", ["occurred_at"], unique=False)

    _backfill_claims()


def _backfill_claims() -> None:
    bind = op.get_bind()
    now = datetime.utcnow()
    pages = bind.execute(sa.text("SELECT id, facts_json, updated_at FROM wiki_pages")).fetchall()
    total = 0
    for page_id, facts_json, updated_at in pages:
        facts = json.loads(facts_json or "{}")
        source_document_id = bind.execute(
            sa.text(
                "SELECT document_id FROM wiki_changes WHERE wiki_page_id = :page_id "
                "AND document_id IS NOT NULL ORDER BY changed_at DESC LIMIT 1"
            ),
            {"page_id": page_id},
        ).scalar()
        for key, value in facts.items():
            text_value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            result = bind.execute(
                sa.text(
                    "INSERT INTO wiki_claims (page_id, key, label, value, status, created_at) "
                    "VALUES (:page_id, :key, :key, :value, 'ACTIVE', :created_at)"
                ),
                {"page_id": page_id, "key": key, "value": text_value, "created_at": updated_at or now},
            )
            if source_document_id is not None:
                bind.execute(
                    sa.text(
                        "INSERT INTO wiki_claim_sources (claim_id, document_id, created_at) "
                        "VALUES (:claim_id, :document_id, :created_at)"
                    ),
                    {"claim_id": result.lastrowid, "document_id": source_document_id, "created_at": now},
                )
            total += 1
    bind.execute(
        sa.text(
            "INSERT INTO wiki_log (occurred_at, operation, description, page_ids_json) "
            "VALUES (:now, 'MIGRATION', :description, '[]')"
        ),
        {"now": now, "description": f"Backfilled {total} claims from {len(pages)} existing wiki pages"},
    )


def downgrade() -> None:
    op.drop_index("ix_wiki_log_occurred_at", table_name="wiki_log")
    op.drop_table("wiki_log")
    op.drop_index("ix_wiki_claim_sources_document_id", table_name="wiki_claim_sources")
    op.drop_index("ix_wiki_claim_sources_claim_id", table_name="wiki_claim_sources")
    op.drop_table("wiki_claim_sources")
    op.drop_index("ix_wiki_claims_page_id", table_name="wiki_claims")
    op.drop_table("wiki_claims")
    with op.batch_alter_table("wiki_pages", recreate="always") as batch_op:
        batch_op.drop_constraint("uq_wiki_pages_page_type_entity_key", type_="unique")
        batch_op.drop_index("ix_wiki_pages_page_type")
        batch_op.drop_column("summary")
        batch_op.drop_column("entity_key")
        batch_op.drop_column("page_type")