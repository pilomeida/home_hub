"""Identity papers' renewal (PLAN step 8a): the reminder each expiring kind declares (a To-Do RENEW_BEFORE_DAYS before
it expires, titled with the holder's first name and the kind, never the number) and the expiry list for the page."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlmodel import Session

from app.categories.entries import category_entries
from app.categories.fields import field_date, load_fields
from app.categories.identity.kinds import LABELS, RENEW_BEFORE_DAYS
from app.categories.reminders import DateReminder, first_name
from app.models.category import Category
from app.models.document import Document


def _whose(document: Document, fields: dict) -> str:
    holder = (fields.get("holder") or "").strip()
    kind = LABELS.get(document.paper_kind or "", "paper").lower()
    return f"{first_name(holder)}'s {kind}" if holder else f"the {kind}"


RENEWAL = DateReminder(
    field="expires_on", days_before=RENEW_BEFORE_DAYS,
    title=lambda doc, fields, when: f"Renew {_whose(doc, fields)} (expires {when.isoformat()})",
    phone=lambda doc, fields, when: f"{_whose(doc, fields)[0].upper()}{_whose(doc, fields)[1:]} expires on "
                                    f"{when.isoformat()}: see To-Dos.",
)


@dataclass
class Paper:
    document: Document
    kind_label: str
    holder: str
    expires_on: Optional[date]
    note: str            # "expired", "renew now", "in 120 days", "" (no expiry)
    late: bool
    soon: bool


def _note(expires: Optional[date], today: date) -> tuple[str, bool, bool]:
    if expires is None:
        return "", False, False
    days = (expires - today).days
    if days < 0:
        return "expired", True, False
    if days <= RENEW_BEFORE_DAYS:
        return f"expires in {days} day{'s' if days != 1 else ''}: renew now", False, True
    return f"expires {expires.isoformat()}", False, False


def papers_by_holder(session: Session, today: date) -> list[tuple[str, list[Paper]]]:
    """Every Identity & Legal paper, grouped by holder, the soonest expiry first."""
    groups: dict[str, list[Paper]] = {}
    for entry in category_entries(session, Category.IDENTITY):
        if entry.document is None:
            continue
        fields = load_fields(entry.document)
        expires = field_date(fields, "expires_on")
        holder = (fields.get("holder") or "").strip() or "Holder not known yet"
        groups.setdefault(holder, []).append(Paper(entry.document, LABELS.get(entry.document.paper_kind or "", "Paper"),
                                                   holder, expires, *_note(expires, today)))
    for papers in groups.values():
        papers.sort(key=lambda p: (p.expires_on is None, p.expires_on or date.max))
    return sorted(groups.items(), key=lambda kv: kv[0].lower())


def next_expiry(session: Session, today: date) -> Optional[Paper]:
    upcoming = [p for _h, ps in papers_by_holder(session, today) for p in ps if p.expires_on and not p.late]
    return min(upcoming, key=lambda p: p.expires_on) if upcoming else None
