"""Insurance's card on the cross-domain '/' Overview."""

from datetime import date

from sqlmodel import Session

from app.categories.base import CardLine, CategoryCard
from app.categories.entries import domain_entries
from app.categories.insurance.policies import policy_sections
from app.models.category import Category


def insurance_overview_card(session: Session, today: date) -> CategoryCard:
    sections = policy_sections(session)
    lines = [CardLine(text=f"{len(cards)} {title.lower()} polic{'y' if len(cards) == 1 else 'ies'}", url="/insurance")
             for title, cards in sections if cards]
    documents = len(domain_entries(session, Category.INSURANCE))
    lines.append(CardLine(text=f"{documents} document{'s' if documents != 1 else ''} on file", url="/insurance"))
    return CategoryCard(label="Insurance", url="/insurance", lines=lines)
