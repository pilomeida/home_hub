"""Generates To-Do entries from transaction due dates."""

from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models.document import Document
from app.models.domain import Domain
from app.models.record import Record
from app.models.todo import Todo
from app.models.transaction import Transaction


def _today() -> date:
    return date.today()


STALE_BILL_DAYS = 45    # a bill due longer ago than this is history (a backfill), not something left to pay


def generate_todo_for_transaction(session: Session, transaction: Transaction) -> Optional[Todo]:
    """Create a "Pay ..." Todo for a transaction's due date, if it has one and the bill is not history: filing an
    old invoice (a backfill of past years) must not fill the to-do list with overdue payments (Pedro, 2026-10-17)."""
    if transaction.due_date is None:
        return None
    if transaction.due_date < _today() - timedelta(days=STALE_BILL_DAYS):
        return None
    todo = Todo(
        title=f"Pay {transaction.description} — {transaction.amount:.2f} {transaction.currency}",
        due_date=transaction.due_date,
        transaction_id=transaction.id,
        domain=Domain.FINANCIALS,
    )
    session.add(todo)
    session.commit()
    session.refresh(todo)
    return todo


def upsert_source_todo(session: Session, source: Document | Record, *, title: str, due_date: date, domain: Domain) -> Todo:
    """Keep at most one OPEN todo per source (document or record; e.g. a
    warranty's renewal reminder), updating it when the source's facts change.
    A todo already marked done is left alone; a new one is created instead."""
    link = Todo.record_id == source.id if isinstance(source, Record) else Todo.document_id == source.id
    todo = session.exec(select(Todo).where(link, Todo.done == False)).first()  # noqa: E712
    if todo is None:
        todo = Todo(title=title, due_date=due_date, domain=domain,
                    record_id=source.id if isinstance(source, Record) else None,
                    document_id=None if isinstance(source, Record) else source.id)
    else:
        todo.title = title
        todo.due_date = due_date
    session.add(todo)
    session.commit()
    session.refresh(todo)
    return todo


def upsert_document_todo(
    session: Session, document: Document, *, title: str, due_date: date, domain: Domain
) -> Todo:
    """Keep at most one OPEN todo per source document (e.g. a warranty's
    renewal reminder), updating it when the document's facts change. A todo
    already marked done is left alone; a new one is created instead."""
    return upsert_source_todo(session, document, title=title, due_date=due_date, domain=domain)
