"""add inbox_items table and review statuses

Revision ID: c4b8e2d91f07
Revises: e7b3d1f4a6c8
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "c4b8e2d91f07"
down_revision: Union[str, Sequence[str], None] = "e7b3d1f4a6c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_STATUS = ("PENDING", "PROCESSED", "NEEDS_ATTENTION")
_NEW_STATUS = _OLD_STATUS + ("PENDING_REVIEW", "DISCARDED")
_OLD_SOURCE = ("MANUAL", "EMAIL", "API")
_NEW_SOURCE = _OLD_SOURCE + ("TELEGRAM",)


def upgrade() -> None:
    with op.batch_alter_table("documents", recreate="always") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum(*_OLD_STATUS, name="documentstatus"),
            type_=sa.Enum(*_NEW_STATUS, name="documentstatus"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "source",
            existing_type=sa.Enum(*_OLD_SOURCE, name="documentsource"),
            type_=sa.Enum(*_NEW_SOURCE, name="documentsource"),
            existing_nullable=False,
        )

    op.create_table(
        "inbox_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("context_text", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("external_ref", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("suggested_domain", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("suggested_category", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("classifier_note", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_inbox_items_document_id"), "inbox_items", ["document_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_inbox_items_document_id"), table_name="inbox_items")
    op.drop_table("inbox_items")
    with op.batch_alter_table("documents", recreate="always") as batch_op:
        batch_op.alter_column(
            "source",
            existing_type=sa.Enum(*_NEW_SOURCE, name="documentsource"),
            type_=sa.Enum(*_OLD_SOURCE, name="documentsource"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "status",
            existing_type=sa.Enum(*_NEW_STATUS, name="documentstatus"),
            type_=sa.Enum(*_OLD_STATUS, name="documentstatus"),
            existing_nullable=False,
        )
