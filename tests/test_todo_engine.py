from datetime import date

from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import Category, Transaction
from app.services.todo_engine import generate_todo_for_transaction


def _make_transaction(session, due_date=None):
    document = Document(
        filename="bill.pdf", file_path="/tmp/bill.pdf", content_hash="hash1",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=87.32, currency="EUR", due_date=due_date,
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)
    return transaction


def test_generate_todo_for_transaction_with_due_date(session):
    transaction = _make_transaction(session, due_date=date(2026, 9, 5))

    todo = generate_todo_for_transaction(session, transaction)

    assert todo is not None
    assert todo.due_date == date(2026, 9, 5)
    assert "EDP" in todo.title
    assert todo.transaction_id == transaction.id


def test_generate_todo_for_transaction_without_due_date_returns_none(session):
    transaction = _make_transaction(session, due_date=None)

    todo = generate_todo_for_transaction(session, transaction)

    assert todo is None
