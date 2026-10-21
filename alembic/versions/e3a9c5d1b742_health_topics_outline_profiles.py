"""Health topics (the baseline tree), outline profiles, and the columns that point at them

Revision ID: e3a9c5d1b742
Revises: d1f6b8c2e547
"""
import sqlalchemy as sa
from alembic import op

revision = "e3a9c5d1b742"
down_revision = "d1f6b8c2e547"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "health_topics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("match_key", sa.String(), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("health_topics.id"), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("merged_into_id", sa.Integer(), sa.ForeignKey("health_topics.id"), nullable=True),
        sa.Column("similar_to_id", sa.Integer(), sa.ForeignKey("health_topics.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_health_topics_match_key", "health_topics", ["match_key"])
    op.create_index("ix_health_topics_parent_id", "health_topics", ["parent_id"])
    op.create_table(
        "health_outline_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider_key", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("layout", sa.String(), nullable=False),
        sa.Column("outline_json", sa.String(), nullable=False),
        sa.Column("confirmed_by", sa.String(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_health_outline_profiles_provider_key", "health_outline_profiles", ["provider_key"])
    with op.batch_alter_table("health_measurements") as b:
        b.add_column(sa.Column("topic_id", sa.Integer(), sa.ForeignKey("health_topics.id", name="fk_health_measurements_topic_id"), nullable=True))
        b.create_index("ix_health_measurements_topic_id", ["topic_id"])
    with op.batch_alter_table("health_entries") as b:
        b.add_column(sa.Column("layout", sa.String(), nullable=True))
        b.add_column(sa.Column("outline_confirmed_by", sa.String(), nullable=True))
        b.add_column(sa.Column("outline_confirmed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("health_entries") as b:
        b.drop_column("outline_confirmed_at")
        b.drop_column("outline_confirmed_by")
        b.drop_column("layout")
    with op.batch_alter_table("health_measurements") as b:
        b.drop_index("ix_health_measurements_topic_id")
        b.drop_column("topic_id")
    op.drop_index("ix_health_outline_profiles_provider_key", table_name="health_outline_profiles")
    op.drop_table("health_outline_profiles")
    op.drop_index("ix_health_topics_parent_id", table_name="health_topics")
    op.drop_index("ix_health_topics_match_key", table_name="health_topics")
    op.drop_table("health_topics")
