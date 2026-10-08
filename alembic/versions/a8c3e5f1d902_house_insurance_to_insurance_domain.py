"""move the House insurance_policy documents to the Insurance domain

Revision ID: a8c3e5f1d902
Revises: f2d7b9e4a183

Data only. documents.domain is VARCHAR(10) without a CHECK, so the new INSURANCE value needs no
schema change. Back up the DB before deploying.
"""
from typing import Sequence, Union

from alembic import op
from sqlmodel import Session

revision: str = "a8c3e5f1d902"
down_revision: Union[str, Sequence[str], None] = "f2d7b9e4a183"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.services.insurance_move import move_house_insurance_documents

    print(f"insurance documents moved: {move_house_insurance_documents(Session(bind=op.get_bind()))}")


def downgrade() -> None:
    raise NotImplementedError("restore the pre-move database backup")
