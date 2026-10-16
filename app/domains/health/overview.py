"""Health's card on the cross-domain '/' Overview. Counts only: nothing clinical."""

from datetime import date

from sqlmodel import Session

from app.domains.base import CardLine, DomainCard
from app.domains.health.browse import facets
from app.domains.entries import domain_entries
from app.models.domain import Domain


def health_overview_card(session: Session, today: date) -> DomainCard:
    n = len(domain_entries(session, Domain.HEALTH))
    f = facets(session)
    lines = [CardLine(text=f"{n} record{'s' if n != 1 else ''} on file", url="/health/records")]
    if f.needs_check:
        lines.append(CardLine(text=f"{f.needs_check} to check", url="/health/records?check=1", attention=True))
    if f.proposed_areas:
        lines.append(CardLine(text=f"{f.proposed_areas} new area{'s' if f.proposed_areas != 1 else ''} to approve",
                              url="/health/areas", attention=True))
    return DomainCard(label="Health", url="/health/records", lines=lines)
