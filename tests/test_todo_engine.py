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
        document_id=document.id, description="EDP", category=Category.ELECTRICITY,
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


def test_generate_todo_for_transaction_sets_financials_domain(session):
    from app.models.domain import Domain

    transaction = _make_transaction(session, due_date=date(2026, 9, 5))

    todo = generate_todo_for_transaction(session, transaction)

    assert todo.domain == Domain.FINANCIALS


def test_upsert_document_todo_creates_then_updates_one_open_todo(session):
    from sqlmodel import select

    from app.models.domain import Domain
    from app.models.todo import Todo
    from app.services.todo_engine import upsert_document_todo

    document = Document(filename="w.pdf", file_path="/tmp/w.pdf", content_hash="hash-upsert",
                        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED)
    session.add(document)
    session.commit()
    session.refresh(document)

    first = upsert_document_todo(session, document, title="Renew Boiler warranty", due_date=date(2027, 2, 8), domain=Domain.HOUSE)
    second = upsert_document_todo(session, document, title="Renew Boiler warranty", due_date=date(2028, 2, 8), domain=Domain.HOUSE)

    assert first.id == second.id
    assert second.due_date == date(2028, 2, 8) and second.document_id == document.id and second.domain == Domain.HOUSE
    assert len(session.exec(select(Todo)).all()) == 1


def test_upsert_source_todo_links_records(session):
    from app.models.domain import Domain
    from app.models.record import Record
    from app.services.todo_engine import upsert_source_todo

    record = Record(domain=Domain.HOUSE, category="maintenance_log")
    session.add(record)
    session.commit()
    session.refresh(record)
    todo = upsert_source_todo(session, record, title="Next service", due_date=date(2027, 1, 1), domain=Domain.HOUSE)
    again = upsert_source_todo(session, record, title="Next service", due_date=date(2027, 2, 1), domain=Domain.HOUSE)
    assert todo.id == again.id and again.record_id == record.id and again.document_id is None


def test_a_bill_due_long_ago_is_history_and_gets_no_pay_todo(session, monkeypatch):
    """Filing 50 old invoices (a backfill, 2026-10-17) must not create 50 overdue 'Pay ...' to-dos."""
    monkeypatch.setattr("app.services.todo_engine._today", lambda: date(2026, 10, 17))
    assert generate_todo_for_transaction(session, _make_transaction(session, due_date=date(2026, 9, 10))) is not None   # 37 days: still to pay
    assert generate_todo_for_transaction(session, _make_transaction(session, due_date=date(2026, 9, 2))) is not None    # 45 days: the edge
    assert generate_todo_for_transaction(session, _make_transaction(session, due_date=date(2026, 9, 1))) is None        # 46 days: history
    assert generate_todo_for_transaction(session, _make_transaction(session, due_date=date(2024, 8, 21))) is None
