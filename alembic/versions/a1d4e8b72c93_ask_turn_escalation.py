"""ask_turns.escalation_json: the private-run log of an Ask answer (D18)

Revision ID: a1d4e8b72c93
Revises: e5b9d7f3a826
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1d4e8b72c93"
down_revision: Union[str, Sequence[str], None] = "e5b9d7f3a826"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ask_turns") as b:
        b.add_column(sa.Column("escalation_json", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("ask_turns") as b:
        b.drop_column("escalation_json")
