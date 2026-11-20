"""Health follow-ups: something to do again by a date, with its To-Do reminder (PLAN step 7b)

Revision ID: f6b9d1e3a780
Revises: e5a8c0d2f679
"""
import sqlalchemy as sa
from alembic import op

revision = "f6b9d1e3a780"
down_revision = "e5a8c0d2f679"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "health_follow_ups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("health_people.id"), nullable=False),
        sa.Column("condition_id", sa.Integer(), sa.ForeignKey("health_conditions.id"), nullable=True),
        sa.Column("what", sa.String(), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("source", sa.String(), nullable=False, server_default="person"),
        sa.Column("state", sa.String(), nullable=False, server_default="active"),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("health_entries.id"), nullable=True),
        sa.Column("source_text", sa.String(), nullable=True),
        sa.Column("todo_id", sa.Integer(), sa.ForeignKey("todos.id"), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_health_follow_ups_person_id", "health_follow_ups", ["person_id"])
    op.create_index("ix_health_follow_ups_condition_id", "health_follow_ups", ["condition_id"])


def downgrade() -> None:
    op.drop_index("ix_health_follow_ups_condition_id", "health_follow_ups")
    op.drop_index("ix_health_follow_ups_person_id", "health_follow_ups")
    op.drop_table("health_follow_ups")
