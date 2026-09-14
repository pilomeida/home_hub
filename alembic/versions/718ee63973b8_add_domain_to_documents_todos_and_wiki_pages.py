"""add domain to documents, todos, and wiki_pages

Revision ID: 718ee63973b8
Revises: 650bb8ad7210
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '718ee63973b8'
down_revision: Union[str, Sequence[str], None] = '650bb8ad7210'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("domain", sa.Enum("FINANCIALS", name="domain"), nullable=True))
    with op.batch_alter_table("todos", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("domain", sa.Enum("FINANCIALS", name="domain"), nullable=True))
    with op.batch_alter_table("wiki_pages", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("domain", sa.Enum("FINANCIALS", name="domain"), nullable=True))

    op.execute("UPDATE documents SET domain = 'FINANCIALS' WHERE domain IS NULL")
    op.execute("UPDATE todos SET domain = 'FINANCIALS' WHERE domain IS NULL")
    op.execute("UPDATE wiki_pages SET domain = 'FINANCIALS' WHERE domain IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("documents", recreate="always") as batch_op:
        batch_op.drop_column("domain")
    with op.batch_alter_table("todos", recreate="always") as batch_op:
        batch_op.drop_column("domain")
    with op.batch_alter_table("wiki_pages", recreate="always") as batch_op:
        batch_op.drop_column("domain")
