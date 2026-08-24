"""Aggregation for the Overview screen's demoted 'Household' panel: open
to-dos and recently-changed wiki pages. Finance aggregation (KPIs,
cash-flow, category comparison, needs attention) lives in
overview_service.py -- Documents are bill/statement artifacts, so their
needs-attention query moved there too, not here."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models.todo import Todo
from app.models.wiki import WikiPage

_RECENTLY_CHANGED_DAYS = 7


@dataclass
class HouseholdData:
    open_todos: list[Todo] = field(default_factory=list)
    recently_changed_wiki_pages: list[WikiPage] = field(default_factory=list)


def get_household_data(session: Session, today: Optional[date] = None) -> HouseholdData:
    open_todos = list(
        session.exec(select(Todo).where(Todo.done == False).order_by(Todo.due_date))  # noqa: E712
    )
    cutoff = datetime.utcnow() - timedelta(days=_RECENTLY_CHANGED_DAYS)
    recently_changed = list(session.exec(select(WikiPage).where(WikiPage.updated_at >= cutoff)))
    return HouseholdData(open_todos=open_todos, recently_changed_wiki_pages=recently_changed)
