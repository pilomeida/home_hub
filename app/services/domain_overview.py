"""The cross-domain row of cards on '/': one card per registered domain,
each built by the domain's own overview_card, plus its open-todo count."""

from datetime import date

from sqlmodel import Session

from app.domains.base import CardLine, DomainCard
from app.domains.registry import implemented_domains
from app.services.todo_backlog import open_todo_count


def build_domain_cards(session: Session, today: date) -> list[DomainCard]:
    cards = []
    for spec in implemented_domains():
        try:
            card = spec.overview_card(session, today)
        except Exception as exc:
            # The Overview is the busiest page: one domain's summary failing
            # must degrade to a visible placeholder, not a 500 for everything.
            session.rollback()
            print(f"overview card failed for {spec.domain.value}: {exc}")
            card = DomainCard(label=spec.label, url=spec.home_url,
                              lines=[CardLine("Summary unavailable right now", attention=True)])
        card.open_todos = open_todo_count(session, spec.domain)
        cards.append(card)
    return cards
