"""add ask_conversations, ask_turns, lint_runs, lint_findings

Revision ID: e5a1c7f3b920
Revises: c4b8e2d91f07
Create Date: 2026-09-24 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = "e5a1c7f3b920"
down_revision: Union[str, Sequence[str], None] = "c4b8e2d91f07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DOMAINS = ("FINANCIALS", "HOUSE")  # every Domain member existing at this revision (see app/models/domain.py)
_STR = sqlmodel.sql.sqltypes.AutoString


def upgrade() -> None:
    op.create_table(
        "ask_conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", _STR(), nullable=False),
        sa.Column("started_by", _STR(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_ask_conversations_updated_at", "ask_conversations", ["updated_at"])
    op.create_table(
        "ask_turns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("ask_conversations.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("question", _STR(), nullable=False),
        sa.Column("status", sa.Enum("PENDING", "ANSWERED", "FAILED", name="askstatus"), nullable=False),
        sa.Column("answer_text", _STR(), nullable=True),
        sa.Column("citations_json", _STR(), nullable=False),
        sa.Column("used_raw_sources", sa.Boolean(), nullable=False),
        sa.Column("asked_by", _STR(), nullable=True),
        sa.Column("error", _STR(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("saved_wiki_page_id", sa.Integer(), sa.ForeignKey("wiki_pages.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("conversation_id", "position", name="uq_ask_turns_conversation_position"),
    )
    op.create_index("ix_ask_turns_conversation_id", "ask_turns", ["conversation_id"])
    op.create_table(
        "lint_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trigger", _STR(), nullable=False),
        sa.Column("status", sa.Enum("RUNNING", "SUCCEEDED", "PARTIAL", "FAILED", name="lintrunstatus"), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("new_count", sa.Integer(), nullable=False),
        sa.Column("open_count", sa.Integer(), nullable=False),
        sa.Column("auto_resolved_count", sa.Integer(), nullable=False),
        sa.Column("errors", _STR(), nullable=True),
    )
    op.create_table(
        "lint_findings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fingerprint", _STR(), nullable=False),
        sa.Column("kind", sa.Enum("CONTRADICTION", "STALE_CLAIM", "GAP", "ORPHAN_PAGE", "MISSING_LINK", "STALE_LINK",
                                  "UNSOURCED_CLAIM", "UNINGESTED_SOURCES", "STALE_SAVED_ANSWER",
                                  name="lintfindingkind"), nullable=False),
        sa.Column("domain", sa.Enum(*_DOMAINS, name="domain"), nullable=True),
        sa.Column("summary", _STR(), nullable=False),
        sa.Column("suggested_action", _STR(), nullable=True),
        sa.Column("wiki_page_ids_json", _STR(), nullable=False),
        sa.Column("claim_ids_json", _STR(), nullable=False),
        sa.Column("document_ids_json", _STR(), nullable=False),
        sa.Column("record_ids_json", _STR(), nullable=False),
        sa.Column("status", sa.Enum("OPEN", "DISMISSED", "FIXED", "AUTO_RESOLVED", name="lintfindingstatus"), nullable=False),
        sa.Column("first_seen_run_id", sa.Integer(), sa.ForeignKey("lint_runs.id"), nullable=False),
        sa.Column("last_seen_run_id", sa.Integer(), sa.ForeignKey("lint_runs.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by", _STR(), nullable=True),
    )
    op.create_index("ix_lint_findings_fingerprint", "lint_findings", ["fingerprint"])


def downgrade() -> None:
    op.drop_index("ix_lint_findings_fingerprint", table_name="lint_findings")
    op.drop_table("lint_findings")
    op.drop_table("lint_runs")
    op.drop_index("ix_ask_turns_conversation_id", table_name="ask_turns")
    op.drop_table("ask_turns")
    op.drop_index("ix_ask_conversations_updated_at", table_name="ask_conversations")
    op.drop_table("ask_conversations")
