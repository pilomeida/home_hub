"""House's card on the cross-domain '/' Overview."""

from datetime import date, timedelta

from sqlmodel import Session

from app.domains.base import CardLine, DomainCard
from app.domains.entries import domain_entries
from app.domains.house.categories import HouseCategory
from app.domains.house.items import build_item_cards, describe_age, house_documents
from app.domains.house.warranty import effective_warranty_expiry
from app.models.domain import Domain

WARRANTY_HORIZON_DAYS = 60


def house_overview_card(session: Session, today: date) -> DomainCard:
    cards = build_item_cards(session)
    documents = house_documents(session)
    lines: list[CardLine] = []

    horizon = today + timedelta(days=WARRANTY_HORIZON_DAYS)
    expiring = sorted(
        (c for c in cards if c.warranty_expiry and today <= c.warranty_expiry <= horizon),
        key=lambda c: c.warranty_expiry,
    )
    for card in expiring:
        text = f"{card.name} warranty expires {card.warranty_expiry.strftime('%d %b %Y')}"
        if card.warranty_assumed:
            text += " (assumed)"
        lines.append(CardLine(
            text=text,
            url=f"/house#{card.anchor}",
            attention=card.warranty_state(today) == "due",
        ))

    missing = sum(
        1 for e in domain_entries(session, Domain.HOUSE)
        if e.category == HouseCategory.WARRANTY_INVOICE.value and effective_warranty_expiry(e.fields) is None
    )
    if missing:
        lines.append(CardLine(
            text=f"{missing} warrant{'y' if missing == 1 else 'ies'} missing an expiry date",
            url="/house", attention=True,
        ))

    serviced = [c for c in cards if c.last_serviced]
    if serviced:
        latest = max(serviced, key=lambda c: c.last_serviced)
        lines.append(CardLine(
            text=f"Last maintenance: {latest.name}, {describe_age(latest.last_serviced, today)}",
            url=f"/house#{latest.anchor}",
        ))

    lines.append(CardLine(text=f"{len(documents)} document{'s' if len(documents) != 1 else ''} on file", url="/house"))
    return DomainCard(label="House", url="/house", lines=lines)
