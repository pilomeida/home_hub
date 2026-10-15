"""ask_turns.left_out_json and lint_runs.left_out_json: what Ask / the wiki check could not read (privacy levels)

Revision ID: d4a8c6e2b715
Revises: c2f8b4d6e931
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4a8c6e2b715"
down_revision: Union[str, Sequence[str], None] = "c2f8b4d6e931"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ask_turns") as b:
        b.add_column(sa.Column("left_out_json", sa.String(), nullable=True))
    with op.batch_alter_table("lint_runs") as b:
        b.add_column(sa.Column("left_out_json", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("lint_runs") as b:
        b.drop_column("left_out_json")
    with op.batch_alter_table("ask_turns") as b:
        b.drop_column("left_out_json")
