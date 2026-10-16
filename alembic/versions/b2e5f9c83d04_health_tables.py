"""Health tables: people, areas, entries, entry tags, measurements

Revision ID: b2e5f9c83d04
Revises: a1d4e8b72c93
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2e5f9c83d04"
down_revision: Union[str, Sequence[str], None] = "a1d4e8b72c93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "health_people",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("match_key", sa.String(), nullable=False, index=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "health_areas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False, index=True),
        sa.Column("match_key", sa.String(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("merged_into_id", sa.Integer(), sa.ForeignKey("health_areas.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("kind", "match_key", name="uq_health_areas_kind_key"),
    )
    op.create_table(
        "health_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=False, unique=True, index=True),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("health_people.id"), nullable=True, index=True),
        sa.Column("entry_date", sa.Date(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True, index=True),
        sa.Column("kind", sa.String(), nullable=True, index=True),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("full_text", sa.String(), nullable=True),
        sa.Column("amount_billed", sa.Float(), nullable=True),
        sa.Column("amount_reimbursed", sa.Float(), nullable=True),
        sa.Column("amount_owed", sa.Float(), nullable=True),
        sa.Column("needs_check", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("calendar_event_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "health_entry_tags",
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("health_entries.id"), primary_key=True),
        sa.Column("area_id", sa.Integer(), sa.ForeignKey("health_areas.id"), primary_key=True),
    )
    op.create_table(
        "health_measurements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("health_entries.id"), nullable=False, index=True),
        sa.Column("person_id", sa.Integer(), sa.ForeignKey("health_people.id"), nullable=True, index=True),
        sa.Column("measured_on", sa.Date(), nullable=True),
        sa.Column("test", sa.String(), nullable=False),
        sa.Column("test_key", sa.String(), nullable=False, server_default="", index=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("value_text", sa.String(), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("ref_low", sa.Float(), nullable=True),
        sa.Column("ref_high", sa.Float(), nullable=True),
        sa.Column("out_of_range", sa.Boolean(), nullable=True),
        sa.Column("needs_check", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_text", sa.String(), nullable=True),
    )


def downgrade() -> None:
    for table in ("health_measurements", "health_entry_tags", "health_entries", "health_areas", "health_people"):
        op.drop_table(table)
