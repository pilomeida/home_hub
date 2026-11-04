"""Policy cards: every paper of one policy (same insurer and policy number) on one card, with the premium notices
filed under Bills that name the policy (shown here, filed and counted once, under Bills)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session

from app.domains.entries import SourceEntry, domain_entries
from app.domains.insurance.categories import PART_ORDER, POLICY_PARTS, SECTION_TITLES, InsuranceKind
from app.models.domain import Domain
from app.services.wiki_store import normalize_entity_key

_PART_LABELS = dict(POLICY_PARTS)


@dataclass
class PolicyPaper:
    entry: SourceEntry
    note: str            # what the paper is, in a few words: "general conditions", "premium notice (in Bills)"


@dataclass
class PolicyCard:
    key: str
    covers: str
    insurer: str
    policy_number: str
    product: Optional[str] = None
    insured: Optional[str] = None
    documents: list[PolicyPaper] = field(default_factory=list)

    @property
    def anchor(self) -> str:
        return "policy-" + self.key.replace(" ", "-").replace("/", "-")


def _key(insurer: str, number: str) -> str:
    return f"{insurer.strip().lower()}-{number.strip().lower()}"


def _note(entry: SourceEntry) -> str:
    if entry.paper_kind == InsuranceKind.CLAIM.value:
        return "claim"
    return _PART_LABELS.get(entry.fields.get("part", ""), "policy paper").lower()


def _order(paper: PolicyPaper) -> tuple:
    entry = paper.entry
    rank = (PART_ORDER.get(entry.fields.get("part", "other"), 90) if entry.paper_kind == InsuranceKind.POLICY.value
            else 95 if entry.paper_kind == InsuranceKind.CLAIM.value else 99)
    return rank, entry.created_at


def build_policy_cards(session: Session) -> list[PolicyCard]:
    cards: dict[str, PolicyCard] = {}
    by_number: dict[str, PolicyCard] = {}
    for entry in domain_entries(session, Domain.INSURANCE):
        insurer, number = entry.fields.get("insurer", ""), entry.fields.get("policy_number", "")
        key = _key(insurer, number)
        card = cards.setdefault(key, PolicyCard(key, entry.fields.get("covers", ""), insurer, number))
        card.covers = card.covers or entry.fields.get("covers", "")
        card.product = card.product or entry.fields.get("product") or None
        card.insured = card.insured or entry.fields.get("insured") or None
        card.documents.append(PolicyPaper(entry, _note(entry)))
        by_number.setdefault(normalize_entity_key(number), card)
    # A premium notice is a bill: it lives under Bills and is shown on the policy it names.
    for entry in domain_entries(session, Domain.FINANCIALS):
        number = entry.fields.get("policy_number")
        card = by_number.get(normalize_entity_key(number)) if number else None
        if card is not None:
            card.documents.append(PolicyPaper(entry, "premium notice (in Bills)"))
    for card in cards.values():
        card.documents.sort(key=_order)
    return sorted(cards.values(), key=lambda c: (c.insurer.lower(), c.policy_number))


def policy_sections(session: Session) -> list[tuple[str, list[PolicyCard]]]:
    """All four sections, always, so an empty one says so."""
    cards = build_policy_cards(session)
    return [(title, [c for c in cards if c.covers == covers]) for covers, title in SECTION_TITLES.items()]
