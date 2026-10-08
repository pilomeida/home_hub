"""Insurance's card on the cross-domain '/' Overview."""

from datetime import date

from sqlmodel import Session

from app.domains.base import CardLine, DomainCard
from app.domains.entries import domain_entries
from app.domains.insurance.policies import policy_sections
from app.models.domain import Domain


def insurance_overview_card(session: Session, today: date) -> DomainCard:
    sections = policy_sections(session)
    lines = [CardLine(text=f"{len(cards)} {title.lower()} polic{'y' if len(cards) == 1 else 'ies'}", url="/insurance")
             for title, cards in sections if cards]
    documents = len(domain_entries(session, Domain.INSURANCE))
    lines.append(CardLine(text=f"{documents} document{'s' if documents != 1 else ''} on file", url="/insurance"))
    return DomainCard(label="Insurance", url="/insurance", lines=lines)
