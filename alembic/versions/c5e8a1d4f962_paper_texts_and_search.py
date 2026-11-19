"""Search over papers (PLAN step 1a-b): each filed paper's text, and one FTS5 index over papers

`paper_texts`: one row per document (text, how it was got: pdf_text / ai_read / none / failed, attempts, error).
`paper_search`: an FTS5 table (title, kind, fields, body; diacritics folded) whose rowid is the document's id. It is
derived data: app/services/paper_search.py rewrites a paper's row at filing and the "Paper text" job rebuilds it.
Nothing is filled here: scripts/fill_paper_texts.py and the job fill the papers already on file.

Revision ID: c5e8a1d4f962
Revises: a3c7e1f9b250
"""
import sqlalchemy as sa
from alembic import op

revision = "c5e8a1d4f962"
down_revision = "a3c7e1f9b250"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_texts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("text", sa.String(), nullable=False, server_default=""),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_paper_texts_document_id", "paper_texts", ["document_id"], unique=True)
    op.execute("CREATE VIRTUAL TABLE IF NOT EXISTS paper_search USING fts5("
               "title, kind, fields, body, tokenize='unicode61 remove_diacritics 2')")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS paper_search")
    op.drop_index("ix_paper_texts_document_id", table_name="paper_texts")
    op.drop_table("paper_texts")
