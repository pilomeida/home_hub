"""Apply the saved tier settings to existing rows. Idempotent. Re-run it after
changing settings if rows were created in between; saving a setting already
re-tiers its own type (app.privacy.settings.retier)."""

from __future__ import annotations

from sqlmodel import Session, select

from app.models.privacy import DocumentTypeSetting
from app.privacy.settings import retier


def backfill_tiers(session: Session) -> int:
    changed = 0
    for s in session.exec(select(DocumentTypeSetting)):
        changed += retier(session, s.domain, s.category, s.tier)
    return changed
