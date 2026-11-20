"""Health conditions as records of their own, and which papers are about them (PLAN step 7a)

Revision ID: e5a8c0d2f679
Revises: d4f7b9c1e568
"""
import sqlalchemy as sa
from alembic import op

revision = "e5a8c0d2f679"
down_revision = "d4f7b9c1e568"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "health_conditions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("health_people.id"), nullable=False),
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("subjects.id"), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("match_key", sa.String(), nullable=False),
        sa.Column("started_on", sa.Date(), nullable=True),
        sa.Column("started_note", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="followed"),
        sa.Column("followed_by", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("person_id", "match_key", name="uq_health_conditions_person_key"),
    )
    op.create_index("ix_health_conditions_person_id", "health_conditions", ["person_id"])
    op.create_table(
        "health_entry_conditions",
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("health_entries.id"), primary_key=True),
        sa.Column("condition_id", sa.Integer(), sa.ForeignKey("health_conditions.id"), primary_key=True),
        sa.Column("state", sa.String(), nullable=False, server_default="suggested"),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("health_entry_conditions")
    op.drop_index("ix_health_conditions_person_id", "health_conditions")
    op.drop_table("health_conditions")
