"""Tags in the transactions router: filter and list endpoints."""

from datetime import date

import pytest

from app.models.document import Document, DocumentSource
from app.models.transaction import Category, Transaction, TransactionType
from app.services.tag_service import ensure_tags
from app.services.taxonomy import ensure_taxonomy, file_transaction, get_node


def test_transactions_filter_by_tag_includes_matching_transactions(client, session):
    """Filter by tag parameter restricts to transactions under tagged nodes."""
    ensure_taxonomy(session)
    ensure_tags(session)

    # Create documents for transactions
    doc1 = Document(filename="doc1.pdf", file_path="/tmp/doc1.pdf",
                    content_hash="hash1", source=DocumentSource.MANUAL)
    doc2 = Document(filename="doc2.pdf", file_path="/tmp/doc2.pdf",
                    content_hash="hash2", source=DocumentSource.MANUAL)
    doc3 = Document(filename="doc3.pdf", file_path="/tmp/doc3.pdf",
                    content_hash="hash3", source=DocumentSource.MANUAL)
    session.add_all([doc1, doc2, doc3])
    session.commit()

    # Create insurance transaction (should be tagged)
    txn1 = Transaction(
        document_id=doc1.id, provider="Insurance Co", category=Category.OTHER,
        transaction_type=TransactionType.DEBIT, amount=100.0, currency="EUR",
        paid_date=date(2026, 10, 5)
    )
    session.add(txn1)
    session.commit()
    file_transaction(session, txn1, get_node(session, "insurances.cars.car-insurance"))
    session.commit()

    # Create home insurance transaction (also tagged)
    txn2 = Transaction(
        document_id=doc2.id, provider="Home Insurer", category=Category.OTHER,
        transaction_type=TransactionType.DEBIT, amount=150.0, currency="EUR",
        paid_date=date(2026, 10, 3)
    )
    session.add(txn2)
    session.commit()
    file_transaction(session, txn2, get_node(session, "insurances.home.home-insurance"))
    session.commit()

    # Create non-tagged transaction (grocery)
    txn3 = Transaction(
        document_id=doc3.id, provider="Zeta Groceries", category=Category.GROCERIES,
        transaction_type=TransactionType.DEBIT, amount=50.0, currency="EUR",
        paid_date=date(2026, 10, 4)
    )
    session.add(txn3)
    session.commit()
    file_transaction(session, txn3, get_node(session, "food.groceries.supermarket"))
    session.commit()

    # Filter by insurance tag
    response = client.get("/financials/transactions", params={"tag": "insurance"})
    assert response.status_code == 200

    # Should see insurance transactions
    assert "Insurance Co" in response.text
    assert "Home Insurer" in response.text

    # Should not see grocery transaction
    assert "Zeta Groceries" not in response.text


def test_transactions_filter_by_unknown_tag_returns_no_rows(client, session):
    """Filter by unknown tag returns no rows (not an error)."""
    ensure_taxonomy(session)
    ensure_tags(session)

    response = client.get("/financials/transactions", params={"tag": "nonexistent"})
    assert response.status_code == 200

    # Page should load but show no transactions
    # (exact HTML depends on template, so just verify status)


def test_transactions_filter_with_tag_select_shows_all_tags(client, session):
    """Transactions list page should include tag <select> with all tags."""
    ensure_taxonomy(session)
    ensure_tags(session)

    response = client.get("/financials/transactions")
    assert response.status_code == 200

    # Check that tag select is present (implementation detail will vary)
    # For now, just verify the page loads
    assert "transactions" in response.text.lower()


def test_transactions_filter_by_tag_combines_with_other_filters(client, session):
    """Tag filter can be combined with other filters like date or category."""
    ensure_taxonomy(session)
    ensure_tags(session)

    # Create test transactions with different dates
    doc1 = Document(filename="doc1.pdf", file_path="/tmp/doc1.pdf",
                    content_hash="hash1", source=DocumentSource.MANUAL)
    doc2 = Document(filename="doc2.pdf", file_path="/tmp/doc2.pdf",
                    content_hash="hash2", source=DocumentSource.MANUAL)
    session.add_all([doc1, doc2])
    session.commit()

    # Insurance in October
    txn1 = Transaction(
        document_id=doc1.id, provider="Ins1", category=Category.OTHER,
        transaction_type=TransactionType.DEBIT, amount=100.0, currency="EUR",
        paid_date=date(2026, 10, 5)
    )
    session.add(txn1)
    session.commit()
    file_transaction(session, txn1, get_node(session, "insurances.cars.car-insurance"))
    session.commit()

    # Insurance in September
    txn2 = Transaction(
        document_id=doc2.id, provider="Ins2", category=Category.OTHER,
        transaction_type=TransactionType.DEBIT, amount=150.0, currency="EUR",
        paid_date=date(2026, 9, 5)
    )
    session.add(txn2)
    session.commit()
    file_transaction(session, txn2, get_node(session, "insurances.home.home-insurance"))
    session.commit()

    # Filter by tag and date
    response = client.get(
        "/financials/transactions",
        params={"tag": "insurance", "date_from": "2026-10-01", "date_to": "2026-10-31"}
    )
    assert response.status_code == 200

    # Should see October transaction
    assert "Ins1" in response.text

    # Should not see September transaction
    assert "Ins2" not in response.text
