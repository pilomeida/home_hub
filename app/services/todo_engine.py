"""Generates To-Do entries from transaction due dates."""

from typing import Optional

from sqlmodel import Session

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
