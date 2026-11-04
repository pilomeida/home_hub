"""What may leave the Hub through Telegram, email or the calendar (decision D21).

Administrative facts may; health-record facts never do — for those the message points to the Hub.
Two parts: the label of a document's type (here) and the fields of a document (`channel_fields`, Task 11)."""

from __future__ import annotations

from typing import Mapping, Optional

from sqlmodel import Session

from app.categories.base import CategorySpec
from app.privacy.reach import reach_for

PRIVATE_LABEL = "a private document"


def channel_label(session: Session, category: object, paper_kind: Optional[str], label: Optional[str]) -> Optional[str]:
    """The type's label for a message that leaves the Hub, or 'a private document' when the type is private."""
    if reach_for(session).is_private_type(category, paper_kind):
        return PRIVATE_LABEL
    return label


def channel_fields(spec: CategorySpec, paper_kind: Optional[str], fields: Mapping[str, str]) -> dict[str, str]:
    """The fields that may leave the Hub (Telegram, email, the calendar): an ALLOWLIST. Only keys the domain
    declares for this category and does not mark `private` pass; an unknown key, and anything marked private
    (what was wrong, what was done), never does (decision D21)."""
    allowed = {f.key for f in spec.fields_for(paper_kind) if not f.private}
    return {k: v for k, v in fields.items() if k in allowed}
