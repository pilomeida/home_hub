"""What may leave the Hub through Telegram, email or the calendar (decision D21).

Administrative facts may; health-record facts never do — for those the message points to the Hub.
Two parts: the label of a document's type (here) and the fields of a document (`channel_fields`, Task 11)."""

from __future__ import annotations

from typing import Mapping, Optional

from sqlmodel import Session

from app.domains.base import DomainSpec
from app.privacy.reach import reach_for

PRIVATE_LABEL = "a private document"


def channel_label(session: Session, domain: object, category: Optional[str], label: Optional[str]) -> Optional[str]:
    """The type's label for a message that leaves the Hub, or 'a private document' when the type is private."""
    if reach_for(session).is_private_type(domain, category):
        return PRIVATE_LABEL
    return label


def channel_fields(spec: DomainSpec, category: Optional[str], fields: Mapping[str, str]) -> dict[str, str]:
    """The fields that may leave the Hub (Telegram, email, the calendar): an ALLOWLIST. Only keys the domain
    declares for this category and does not mark `private` pass; an unknown key, and anything marked private
    (what was wrong, what was done), never does (decision D21)."""
    allowed = {f.key for f in spec.fields_for(category) if not f.private}
    return {k: v for k, v in fields.items() if k in allowed}
