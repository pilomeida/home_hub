"""One review queue, "Waiting for you" (PLAN step 3a): the `review_items` table

Revision ID: e3c9a6f1b284
Revises: d7f2b4a8c615
"""
import sqlalchemy as sa
from alembic import op

revision = "e3c9a6f1b284"
down_revision = "d7f2b4a8c615"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("area", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("ref", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("why", sa.String(), nullable=False),
        sa.Column("evidence_url", sa.String(), nullable=True),
        sa.Column("options_json", sa.String(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(), nullable=False, server_default="waiting"),
        sa.Column("snoozed_until", sa.DateTime(), nullable=True),
        sa.Column("answer", sa.String(), nullable=True),
        sa.Column("remembered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    for col in ("area", "kind", "ref", "status"):
        op.create_index(f"ix_review_items_{col}", "review_items", [col])


def downgrade() -> None:
    for col in ("area", "kind", "ref", "status"):
        op.drop_index(f"ix_review_items_{col}", table_name="review_items")
    op.drop_table("review_items")
