"""move the category tree to Pedro's own structure (2026-10-08)

Revision ID: e9c1a7d3b542
Revises: d4b8e2a6c931

Data only (plus seeding the tags on the final tree): creates the nodes the current seed adds, repoints transactions, merchants and
budgets (app/services/taxonomy_slugmap.py), then drops the nodes nothing points at. Retired
nodes' transactions land in Unsorted. Back up the DB before deploying.
"""
from typing import Sequence, Union

from alembic import op
from sqlmodel import Session

revision: str = "e9c1a7d3b542"
down_revision: Union[str, Sequence[str], None] = "d4b8e2a6c931"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.services.taxonomy_migration import remap_to_current_tree

    from app.services.tag_service import ensure_tags

    session = Session(bind=op.get_bind())
    report = remap_to_current_tree(session)
    ensure_tags(session)  # tags sit on the final tree; edits to tag_seed.py ship with a migration like this
    print(f"category tree remap: {report}")


def downgrade() -> None:
    # The old tree cannot be rebuilt from the new one (merges, retirements). Restore the backup.
    raise NotImplementedError("restore the pre-remap database backup")
