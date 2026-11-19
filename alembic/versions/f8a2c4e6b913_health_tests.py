"""Known lab tests and the wordings that name them (PLAN step 5b)

Revision ID: f8a2c4e6b913
Revises: e3c9a6f1b284
"""
import sqlalchemy as sa
from alembic import op

revision = "f8a2c4e6b913"
down_revision = "e3c9a6f1b284"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "health_tests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("unit_key", sa.String(), nullable=False, server_default=""),
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("merged_into_id", sa.Integer(), sa.ForeignKey("health_tests.id"), nullable=True),
        sa.Column("similar_to_id", sa.Integer(), sa.ForeignKey("health_tests.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_health_tests_unit_key", "health_tests", ["unit_key"])
    op.create_table(
        "health_test_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("test_id", sa.Integer(), sa.ForeignKey("health_tests.id"), nullable=False),
        sa.Column("wording", sa.String(), nullable=False),
        sa.Column("wording_key", sa.String(), nullable=False),
        sa.Column("unit_key", sa.String(), nullable=False, server_default=""),
        sa.Column("source", sa.String(), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("wording_key", "unit_key", name="uq_health_test_aliases_wording_unit"),
    )
    op.create_index("ix_health_test_aliases_test_id", "health_test_aliases", ["test_id"])
    op.create_index("ix_health_test_aliases_wording_key", "health_test_aliases", ["wording_key"])
    op.add_column("health_measurements", sa.Column("test_id", sa.Integer(), nullable=True))   # additive; no table rebuild
    op.create_index("ix_health_measurements_test_id", "health_measurements", ["test_id"])


def downgrade() -> None:
    op.drop_index("ix_health_measurements_test_id", table_name="health_measurements")
    with op.batch_alter_table("health_measurements") as batch:
        batch.drop_column("test_id")
    op.drop_table("health_test_aliases")
    op.drop_table("health_tests")
