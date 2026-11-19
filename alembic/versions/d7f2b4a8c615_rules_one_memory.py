"""One memory of past answers (PLAN step 2a): the `rules` table, filled from provider_rules and document_fingerprints

Every provider rule becomes a person's rule that looks at the bank text by its provider key and answers a spending
leaf; every recognition fingerprint becomes a person's rule that looks at the sender, the words or the file name and
answers a kind of paper (hits and notes kept). Nothing is lost: the two old tables stay, no longer read.

Revision ID: d7f2b4a8c615
Revises: c5e8a1d4f962
"""
import sqlalchemy as sa
from alembic import op

revision = "d7f2b4a8c615"
down_revision = "c5e8a1d4f962"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("looks_at", sa.String(), nullable=False),
        sa.Column("how", sa.String(), nullable=False),
        sa.Column("pattern", sa.String(), nullable=False),
        sa.Column("merchant", sa.String(), nullable=True),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("category_nodes.id"), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("paper_kind", sa.String(), nullable=True),
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("subjects.id"), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(), nullable=False, server_default="person"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("approved_by", sa.String(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("example", sa.String(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_hit_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_rules_looks_at", "rules", ["looks_at"])
    op.create_index("ix_rules_pattern", "rules", ["pattern"])
    op.execute("""
        INSERT INTO rules (looks_at, how, pattern, node_id, source, enabled, approved_by, approved_at, example, note,
                           hits, priority, created_at)
        SELECT 'description', 'key', key, node_id, 'person', 1, 'moved from provider rules', created_at, example,
               'Filed by its bank text (moved from the provider rules on 2026-11-19)', 0, 0, created_at
        FROM provider_rules""")
    op.execute("""
        INSERT INTO rules (looks_at, how, pattern, category, paper_kind, source, enabled, approved_by, approved_at,
                           note, hits, last_hit_at, priority, created_at)
        SELECT kind, 'contains', pattern, domain, category, 'person', 1, created_by, created_at, note, hits,
               last_hit_at, 0, created_at
        FROM document_fingerprints""")


def downgrade() -> None:
    op.drop_index("ix_rules_pattern", table_name="rules")
    op.drop_index("ix_rules_looks_at", table_name="rules")
    op.drop_table("rules")
