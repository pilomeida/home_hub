"""Aggregation queries backing the dashboard view."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models.document import Document, DocumentStatus
from app.models.todo import Todo
from app.models.transaction import Transaction, TransactionType
from app.models.wiki import WikiPage

_RECENTLY_CHANGED_DAYS = 7


@dataclass
class DashboardData:
    spend_this_month: dict[str, float]
    spend_last_month: dict[str, float]
    open_todos: list[Todo] = field(default_factory=list)
    recently_changed_wiki_pages: list[WikiPage] = field(default_factory=list)
    needs_attention_documents: list[Document] = field(default_factory=list)


def _spend_by_category(session: Session, period: str) -> dict[str, float]:
    statement = select(Transaction).where(
        Transaction.statement_period == period,
        Transaction.transaction_type == TransactionType.DEBIT,
    )
    totals: dict[str, float] = {}
    for txn in session.exec(statement):
        totals[txn.category.value] = totals.get(txn.category.value, 0.0) + txn.amount
    return totals


def get_dashboard_data(session: Session, today: Optional[date] = None) -> DashboardData:
    today = today or date.today()
    this_period = f"{today.year:04d}-{today.month:02d}"
    last_month_date = date(today.year, today.month, 1) - timedelta(days=1)
    last_period = f"{last_month_date.year:04d}-{last_month_date.month:02d}"

    open_todos = list(
        session.exec(select(Todo).where(Todo.done == False).order_by(Todo.due_date))  # noqa: E712
    )
    needs_attention = list(
        session.exec(
            select(Document).where(
                Document.status.in_([DocumentStatus.NEEDS_ATTENTION, DocumentStatus.PENDING])
            )
        )
    )
    cutoff = datetime.utcnow() - timedelta(days=_RECENTLY_CHANGED_DAYS)
    recently_changed = list(session.exec(select(WikiPage).where(WikiPage.updated_at >= cutoff)))

    return DashboardData(
        spend_this_month=_spend_by_category(session, this_period),
        spend_last_month=_spend_by_category(session, last_period),
        open_todos=open_todos,
        recently_changed_wiki_pages=recently_changed,
        needs_attention_documents=needs_attention,
    )
