"""Generates To-Do entries from transaction due dates."""

from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.models.document import Document
from app.models.domain import Domain
from app.models.todo import Todo
from app.models.transaction import Transaction


def generate_todo_for_transaction(session: Session, transaction: Transaction) -> Optional[Todo]:
    """Create a Todo for a transaction's due date, if it has one."""
    if transaction.due_date is None:
        return None
    todo = Todo(
        title=f"Pay {transaction.provider} — {transaction.amount:.2f} {transaction.currency}",
        due_date=transaction.due_date,
        transaction_id=transaction.id,
        domain=Domain.FINANCIALS,
    )
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
    todo = session.exec(
        select(Todo).where(Todo.document_id == document.id, Todo.done == False)  # noqa: E712
    ).first()
    if todo is None:
        todo = Todo(title=title, due_date=due_date, domain=domain, document_id=document.id)
    else:
        todo.title = title
        todo.due_date = due_date
    session.add(todo)
    session.commit()
    session.refresh(todo)
    return todo
