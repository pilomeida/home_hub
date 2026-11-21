"""Ask's actions: what each answer did (Undo) or proposed (Confirm / Cancel) (PLAN step 10g, Pedro's yes 2026-11-21)

Revision ID: d1a7e3c5f920
Revises: c9e4a2f6d018
"""
import sqlalchemy as sa
from alembic import op

revision = "d1a7e3c5f920"
down_revision = "c9e4a2f6d018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ask_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("turn_id", sa.Integer(), sa.ForeignKey("ask_turns.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("params_json", sa.String(), nullable=False, server_default="{}"),
        sa.Column("result_json", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decided_by", sa.String(), nullable=True),
    )
    op.create_index("ix_ask_actions_turn_id", "ask_actions", ["turn_id"])


def downgrade() -> None:
    op.drop_index("ix_ask_actions_turn_id", table_name="ask_actions")
    op.drop_table("ask_actions")
