"""PLAN step 12a: areas and kinds Pedro approves (paper_types); the family flag on subjects; rooms are a subject kind

Revision ID: a12f3c5e7b90
Revises: e3b9f1d7a264
"""
import sqlalchemy as sa
from alembic import op

revision = "a12f3c5e7b90"
down_revision = "e3b9f1d7a264"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("area", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=True),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False, server_default=""),
        sa.Column("created_from_document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("area", "kind", name="uq_paper_types_area_kind"),
    )
    op.create_index("ix_paper_types_area", "paper_types", ["area"])
    with op.batch_alter_table("subjects") as batch:
        batch.add_column(sa.Column("household", sa.Boolean(), nullable=False, server_default=sa.false()))
    # subjects.kind is VARCHAR(12) with no CHECK (SQLite): the new ROOM member needs no change.


def downgrade() -> None:
    with op.batch_alter_table("subjects") as batch:
        batch.drop_column("household")
    op.drop_index("ix_paper_types_area", table_name="paper_types")
    op.drop_table("paper_types")
