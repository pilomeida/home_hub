"""add doc_type to documents

Revision ID: 21636872a4f1
Revises: 7ce9ad97895f
Create Date: 2026-08-18 11:53:01.052188

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '21636872a4f1'
down_revision: Union[str, Sequence[str], None] = '7ce9ad97895f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("doc_type", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("documents", recreate="always") as batch_op:
        batch_op.drop_column("doc_type")
