from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import Category, Transaction
from app.services.dedup import find_duplicate_transaction, find_existing_document_by_hash


def test_find_existing_document_by_hash_returns_match(session):
    document = Document(
        filename="bill.pdf", file_path="/tmp/bill.pdf", content_hash="abc123",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(document)
    session.commit()

    found = find_existing_document_by_hash(session, "abc123")

    assert found is not None
    assert found.filename == "bill.pdf"


def test_find_existing_document_by_hash_returns_none_when_missing(session):
    assert find_existing_document_by_hash(session, "does-not-exist") is None


def test_find_duplicate_transaction_matches_provider_and_period(session):
    document = Document(
        filename="bill.pdf", file_path="/tmp/bill.pdf", content_hash="hash1",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=50.0, currency="EUR", statement_period="2026-08",
    )
    session.add(transaction)
    session.commit()

    found = find_duplicate_transaction(session, provider="EDP", statement_period="2026-08")

    assert found is not None
    assert found.id == transaction.id


def test_find_duplicate_transaction_returns_none_without_statement_period(session):
    assert find_duplicate_transaction(session, provider="EDP", statement_period=None) is None
