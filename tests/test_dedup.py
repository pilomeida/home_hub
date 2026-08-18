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


def test_find_duplicate_transaction_ignores_statement_derived_transactions(session):
    """A statement line item (Document.doc_type == 'statement') sharing
    provider/period with an unrelated bill must not be treated as a
    duplicate — only bill-derived transactions count."""
    statement_document = Document(
        filename="statement.pdf", file_path="/tmp/statement.pdf", content_hash="hash-stmt",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED, doc_type="statement",
    )
    session.add(statement_document)
    session.commit()
    session.refresh(statement_document)

    session.add(Transaction(
        document_id=statement_document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=87.32, currency="EUR", statement_period="2026-07",
    ))
    session.commit()

    found = find_duplicate_transaction(session, provider="EDP", statement_period="2026-07")

    assert found is None


def test_find_duplicate_transaction_matches_bill_derived_transaction(session):
    """A bill-derived transaction (Document.doc_type == 'bill') sharing
    provider/period must still be detected as a duplicate."""
    bill_document = Document(
        filename="bill.pdf", file_path="/tmp/bill.pdf", content_hash="hash-bill-typed",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED, doc_type="bill",
    )
    session.add(bill_document)
    session.commit()
    session.refresh(bill_document)

    transaction = Transaction(
        document_id=bill_document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=87.32, currency="EUR", statement_period="2026-07",
    )
    session.add(transaction)
    session.commit()

    found = find_duplicate_transaction(session, provider="EDP", statement_period="2026-07")

    assert found is not None
    assert found.id == transaction.id


def test_find_duplicate_transaction_matches_when_doc_type_is_null(session):
    """A pre-migration Document with doc_type=None must still participate in
    dedup — NULL means 'not a statement' (every document created before
    this branch was necessarily a bill), not 'excluded'."""
    legacy_document = Document(
        filename="legacy-bill.pdf", file_path="/tmp/legacy-bill.pdf", content_hash="hash-legacy-typed",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    assert legacy_document.doc_type is None
    session.add(legacy_document)
    session.commit()
    session.refresh(legacy_document)

    transaction = Transaction(
        document_id=legacy_document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=87.32, currency="EUR", statement_period="2026-07",
    )
    session.add(transaction)
    session.commit()

    found = find_duplicate_transaction(session, provider="EDP", statement_period="2026-07")

    assert found is not None
    assert found.id == transaction.id
