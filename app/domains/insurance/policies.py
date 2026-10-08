"""Policy cards: every document of one policy (same insurer and policy number) on one card."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session

from app.domains.entries import SourceEntry, domain_entries
from app.domains.insurance.categories import KIND_ORDER, SECTION_TITLES
from app.models.domain import Domain


@dataclass
class PolicyCard:
    key: str
    category: str
    insurer: str
    policy_number: str
    product: Optional[str] = None
    insured: Optional[str] = None
    documents: list[SourceEntry] = field(default_factory=list)

    @property
    def anchor(self) -> str:
        return "policy-" + self.key.replace(" ", "-").replace("/", "-")


def _key(insurer: str, number: str) -> str:
    return f"{insurer.strip().lower()}-{number.strip().lower()}"


def build_policy_cards(session: Session) -> list[PolicyCard]:
    cards: dict[str, PolicyCard] = {}
    for entry in domain_entries(session, Domain.INSURANCE):
        insurer, number = entry.fields.get("insurer", ""), entry.fields.get("policy_number", "")
        key = _key(insurer, number)
        card = cards.setdefault(key, PolicyCard(key, entry.category or "", insurer, number))
        card.product = card.product or entry.fields.get("product") or None
        card.insured = card.insured or entry.fields.get("insured") or None
        card.documents.append(entry)
    for card in cards.values():
        card.documents.sort(key=lambda e: (KIND_ORDER.get(e.fields.get("document_kind", "other"), 99), e.created_at))
    return sorted(cards.values(), key=lambda c: (c.insurer.lower(), c.policy_number))


def policy_sections(session: Session) -> list[tuple[str, list[PolicyCard]]]:
    """All four sections, always, so an empty one says so."""
    cards = build_policy_cards(session)
    return [(title, [c for c in cards if c.category == category]) for category, title in SECTION_TITLES.items()]
