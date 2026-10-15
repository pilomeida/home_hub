"""document_type_settings, privacy_settings, tier_changes (tier wiring as Hub configuration)

Revision ID: a8c4e2f6b913
Revises: f7b3d5e9a182
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8c4e2f6b913"
down_revision: Union[str, Sequence[str], None] = "f7b3d5e9a182"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_type_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), nullable=True),
        sa.Column("redact", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("domain", "category", name="uq_document_type_settings_type"),
    )
    op.create_table(
        "privacy_settings",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "tier_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("domain", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("what", sa.String(), nullable=False),
        sa.Column("old_value", sa.String(), nullable=True),
        sa.Column("new_value", sa.String(), nullable=True),
        sa.Column("changed_by", sa.String(), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tier_changes")
    op.drop_table("privacy_settings")
    op.drop_table("document_type_settings")
