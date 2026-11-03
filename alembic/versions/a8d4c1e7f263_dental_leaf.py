"""The new leaf Health > Care > Dental (dental bills are Bills, filed under Health)

Revision ID: a8d4c1e7f263
Revises: e3a9c5d1b742
"""
from typing import Sequence, Union

from alembic import op
from sqlmodel import Session

revision: str = "a8d4c1e7f263"
down_revision: Union[str, Sequence[str], None] = "e3a9c5d1b742"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.services.taxonomy import ensure_taxonomy
    ensure_taxonomy(Session(bind=op.get_bind()))  # creates the new leaf


def downgrade() -> None:
    pass  # the leaf may already hold transactions; an unused leaf is harmless
