"""House item cards and Reference section.

Every document/record about one appliance/gear item (manual, warranty,
maintenance logs) is grouped by its item_name into one card; floor plans
and ownership documents form the Reference section. Built from
domain_entries() (documents and records alike, the source of truth); an
item's wiki page is linked, not read."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.domains.entries import SourceEntry, domain_entries
from app.domains.fields import field_date
from app.domains.house.categories import (
    ITEM_CATEGORIES, ITEM_KIND_CATEGORIES, ITEM_PAGE_TYPE, HouseCategory,
)
from app.domains.house.warranty import REMINDER_LEAD_DAYS, effective_warranty_expiry
from app.models.document import Document, DocumentStatus
from app.models.domain import Domain
from app.services.wiki_store import PageRef, find_page, normalize_entity_key

KIND_SECTION_TITLES = {
    HouseCategory.HOUSE_APPLIANCE.value: "House Appliances",
    HouseCategory.OUTDOOR_GEAR.value: "Outdoor Gear",
}
OTHER_ITEMS_TITLE = "Other items"
NO_ROOM_TITLE = "No room set"
REFERENCE_TITLES = {
    HouseCategory.FLOOR_PLAN.value: "Floor plans",
    HouseCategory.OWNERSHIP_DOCUMENT.value: "Ownership documents",
    HouseCategory.INSURANCE_POLICY.value: "Insurance policies",
}


@dataclass
class ItemCard:
    key: str
    name: str
    kind: Optional[str] = None
    room: Optional[str] = None
    documents: list[SourceEntry] = field(default_factory=list)
    warranty_expiry: Optional[date] = None
    warranty_assumed: bool = False
    last_serviced: Optional[date] = None
    wiki_page_id: Optional[int] = None

    @property
    def anchor(self) -> str:
        return "item-" + self.key.replace(" ", "-")

    def warranty_state(self, today: date) -> Optional[str]:
        if self.warranty_expiry is None:
            return None
        if self.warranty_expiry < today:
            return "lapsed"
        if self.warranty_expiry <= today + timedelta(days=REMINDER_LEAD_DAYS):
            return "due"
        return "ok"


def house_documents(session: Session) -> list[Document]:
    return list(session.exec(
        select(Document).where(
            Document.domain == Domain.HOUSE,
            Document.status.in_([DocumentStatus.PROCESSED, DocumentStatus.NEEDS_ATTENTION]),
        ).order_by(Document.created_at, Document.id)
    ).all())


def _later(current: Optional[date], candidate: Optional[date]) -> Optional[date]:
    if candidate is None:
        return current
    return candidate if current is None or candidate > current else current


def build_item_cards(session: Session) -> list[ItemCard]:
    cards: dict[str, ItemCard] = {}
    for entry in domain_entries(session, Domain.HOUSE):
        if entry.category not in ITEM_CATEGORIES:
            continue
        fields = entry.fields
        name = (fields.get("item_name") or "").strip()
        if not name:
            continue
        key = normalize_entity_key(name)
        card = cards.setdefault(key, ItemCard(key=key, name=name))
        card.name = name
        card.documents.append(entry)
        if entry.category in ITEM_KIND_CATEGORIES:
            card.kind = entry.category
        if fields.get("room"):
            card.room = fields["room"]
        warranty = effective_warranty_expiry(fields) if entry.category == HouseCategory.WARRANTY_INVOICE.value else None
        if warranty is not None and (card.warranty_expiry is None or warranty.date > card.warranty_expiry):
            card.warranty_expiry, card.warranty_assumed = warranty.date, warranty.assumed
        card.last_serviced = _later(card.last_serviced, field_date(fields, "service_date"))
    for card in cards.values():
        page = find_page(session, PageRef(ITEM_PAGE_TYPE, card.name, card.key))
        card.wiki_page_id = page.id if page is not None else None
    return sorted(cards.values(), key=lambda c: c.name.lower())


def group_item_cards(cards: list[ItemCard], group_by: str) -> list[tuple[str, list[ItemCard]]]:
    if group_by == "room":
        rooms: dict[str, list[ItemCard]] = {}
        for card in cards:
            rooms.setdefault(card.room or NO_ROOM_TITLE, []).append(card)
        titles = sorted((t for t in rooms if t != NO_ROOM_TITLE), key=str.lower)
        if NO_ROOM_TITLE in rooms:
            titles.append(NO_ROOM_TITLE)
        return [(title, rooms[title]) for title in titles]
    sections = [(title, [c for c in cards if c.kind == kind]) for kind, title in KIND_SECTION_TITLES.items()]
    others = [c for c in cards if c.kind is None]
    if others:
        sections.append((OTHER_ITEMS_TITLE, others))
    return sections


def reference_sections(session: Session) -> list[tuple[str, list[SourceEntry]]]:
    entries = domain_entries(session, Domain.HOUSE)
    return [
        (title, [e for e in entries if e.category == category])
        for category, title in REFERENCE_TITLES.items()
    ]


def describe_age(since: date, today: date) -> str:
    days = (today - since).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 60:
        return f"{days} days ago"
    if days < 730:
        return f"{days // 30} months ago"
    return f"{days // 365} years ago"
