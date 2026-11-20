"""The cars (PLAN step 8c): one card per plate with its papers, its next inspection, road tax and service dates
(soon in amber, past in red) and the insurance policies that name it; and each dated kind's reminder."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlmodel import Session

from app.categories.entries import category_entries
from app.categories.fields import field_date, load_fields
from app.categories.reminders import DateReminder
from app.categories.vehicles.handler import plate_key
from app.categories.vehicles.kinds import (
    INSPECTION_BEFORE, LABELS, ROAD_TAX_BEFORE, SERVICE_BEFORE, VehicleKind,
)
from app.models.category import Category
from app.models.document import Document


def _car(fields: dict) -> str:
    return " ".join(filter(None, [fields.get("vehicle", "").strip(), fields.get("plate", "")])) or "the car"


def _reminder(field_key: str, before: int, todo: str, phone: str) -> DateReminder:
    return DateReminder(field=field_key, days_before=before,
                        title=lambda doc, f, when: f"{todo} for {_car(f)} (due {when.isoformat()})",
                        phone=lambda doc, f, when: f"{phone} for {_car(f)} is due on {when.isoformat()}: see To-Dos.")


INSPECTION = _reminder("next_inspection_on", INSPECTION_BEFORE, "Book the inspection (IPO)", "The inspection (IPO)")
ROAD_TAX = _reminder("road_tax_due_on", ROAD_TAX_BEFORE, "Pay the road tax (IUC)", "The road tax (IUC)")
SERVICE = _reminder("next_service_on", SERVICE_BEFORE, "Book the service", "The service")

DATES = (("Inspection", "next_inspection_on", INSPECTION_BEFORE), ("Road tax", "road_tax_due_on", ROAD_TAX_BEFORE),
         ("Service", "next_service_on", SERVICE_BEFORE))


@dataclass
class NextDate:
    label: str
    when: date
    late: bool
    soon: bool


@dataclass
class CarCard:
    plate: str
    vehicle: str = ""
    papers: list[tuple[Document, str, str]] = field(default_factory=list)      # (paper, kind label, its date)
    dates: list[NextDate] = field(default_factory=list)
    policies: list = field(default_factory=list)                                 # Insurance's PolicyCard

    @property
    def title(self) -> str:
        return " ".join(filter(None, [self.vehicle, self.plate])) or "Plate not known yet"


def _bare(text: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", (text or "").upper())


def cars(session: Session, today: date) -> list[CarCard]:
    from app.categories.insurance.policies import build_policy_cards
    cards: dict[str, CarCard] = {}
    latest: dict[tuple[str, str], date] = {}
    for entry in category_entries(session, Category.VEHICLES):
        if entry.document is None:
            continue
        fields = load_fields(entry.document)
        plate = plate_key(fields.get("plate", ""))
        card = cards.setdefault(plate, CarCard(plate))
        card.vehicle = card.vehicle or fields.get("vehicle", "")
        card.papers.append((entry.document, LABELS.get(entry.document.paper_kind or "", "Paper"),
                            fields.get("paper_date", "")))
        for _label, key, _before in DATES:
            when = field_date(fields, key)
            if when is not None and when > latest.get((plate, key), date.min):
                latest[(plate, key)] = when                       # the newest paper's date is the one that holds
    policies = build_policy_cards(session)
    for card in cards.values():
        for label, key, before in DATES:
            when = latest.get((card.plate, key))
            if when is not None:
                days = (when - today).days
                card.dates.append(NextDate(label, when, days < 0, 0 <= days <= before))
        card.dates.sort(key=lambda d: d.when)
        card.papers.sort(key=lambda p: p[2] or p[0].created_at.strftime("%Y-%m-%d"), reverse=True)
        bare = _bare(card.plate)
        card.policies = [p for p in policies if bare and bare in _bare(f"{p.insured or ''} {p.product or ''}")]
    return sorted(cards.values(), key=lambda c: c.title.lower())


def next_date(session: Session, today: date) -> Optional[tuple[CarCard, NextDate]]:
    upcoming = [(c, d) for c in cars(session, today) for d in c.dates if not d.late]
    return min(upcoming, key=lambda cd: cd[1].when) if upcoming else None
