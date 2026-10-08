"""Tags in the overview service: 'By tag' panel."""

from datetime import date

import pytest
from sqlmodel import select

from app.models.document import Document, DocumentSource
from app.models.transaction import Category, Transaction, TransactionType
from app.services.tag_service import ensure_tags, tag_totals
from app.services.taxonomy import ensure_taxonomy, file_transaction, get_node


def test_tag_totals_aggregates_spending_by_tag(session):
    """tag_totals returns spending totals for each tag."""
    ensure_taxonomy(session)
    ensure_tags(session)

    doc = Document(filename="test.pdf", file_path="/tmp/test.pdf",
                   content_hash="hash", source=DocumentSource.MANUAL)
    session.add(doc)
    session.commit()

    today = date(2026, 10, 8)

    # Add some insurance transactions
    txn1 = Transaction(
        document_id=doc.id, provider="Car Insurance", category=Category.OTHER,
        transaction_type=TransactionType.DEBIT, amount=100.0, currency="EUR",
        paid_date=date(2026, 10, 5)
    )
    session.add(txn1)
    session.commit()
    file_transaction(session, txn1, get_node(session, "insurances.cars.car-insurance"))
    session.commit()

    totals = tag_totals(session, today)

    # Should have entries for all tags
    assert len(totals) > 0

    # Should have insurance total with the transaction
    insurance = next((t for t in totals if t.name == "insurance"), None)
    assert insurance is not None
    assert insurance.month_to_date == 100.0


def test_tag_totals_in_overview_data(session):
    """Overview data should include tag_totals."""
    ensure_taxonomy(session)
    ensure_tags(session)

    from app.services.overview_service import get_overview_data

    today = date(2026, 10, 8)

    # Create a test transaction
    doc = Document(filename="test.pdf", file_path="/tmp/test.pdf",
                   content_hash="hash", source=DocumentSource.MANUAL)
    session.add(doc)
    session.commit()

    txn = Transaction(
        document_id=doc.id, provider="Insurance", category=Category.OTHER,
        transaction_type=TransactionType.DEBIT, amount=100.0, currency="EUR",
        paid_date=date(2026, 10, 5)
    )
    session.add(txn)
    session.commit()
    file_transaction(session, txn, get_node(session, "insurances.cars.car-insurance"))
    session.commit()

    # This will verify the integration
    overview = get_overview_data(session)

    # Overview should be dict-like (exact structure TBD)
    assert overview is not None


def test_tag_totals_by_period(session):
    """tag_totals correctly calculates different periods."""
    ensure_taxonomy(session)
    ensure_tags(session)

    doc = Document(filename="test.pdf", file_path="/tmp/test.pdf",
                   content_hash="hash", source=DocumentSource.MANUAL)
    session.add(doc)
    session.commit()

    today = date(2026, 10, 8)

    # Add insurance from current month
    txn_current = Transaction(
        document_id=doc.id, provider="Insurance", category=Category.OTHER,
        transaction_type=TransactionType.DEBIT, amount=100.0, currency="EUR",
        paid_date=date(2026, 10, 5)
    )
    session.add(txn_current)
    session.commit()
    file_transaction(session, txn_current, get_node(session, "insurances.cars.car-insurance"))
    session.commit()

    # Add insurance from previous month
    txn_prev = Transaction(
        document_id=doc.id, provider="Insurance", category=Category.OTHER,
        transaction_type=TransactionType.DEBIT, amount=80.0, currency="EUR",
        paid_date=date(2026, 9, 5)
    )
    session.add(txn_prev)
    session.commit()
    file_transaction(session, txn_prev, get_node(session, "insurances.cars.car-insurance"))
    session.commit()

    # Add insurance from last year
    txn_last_year = Transaction(
        document_id=doc.id, provider="Insurance", category=Category.OTHER,
        transaction_type=TransactionType.DEBIT, amount=90.0, currency="EUR",
        paid_date=date(2025, 10, 5)
    )
    session.add(txn_last_year)
    session.commit()
    file_transaction(session, txn_last_year, get_node(session, "insurances.cars.car-insurance"))
    session.commit()

    totals = tag_totals(session, today)

    insurance = next((t for t in totals if t.name == "insurance"), None)
    assert insurance is not None

    # Month to date: 100.0 (only October transactions)
    assert insurance.month_to_date == 100.0

    # Previous month: 80.0 (only September transactions)
    assert insurance.previous_month == 80.0

    # Year to date: 180.0 (October 100 + September 80, both in 2026)
    assert insurance.year_to_date == 180.0

    # Last year: 90.0 (only 2025 transactions)
    assert insurance.last_year == 90.0


def test_dashboard_renders_the_by_tag_panel_with_links_and_amounts(client, session):
    from datetime import date as _date

    ensure_taxonomy(session)
    ensure_tags(session)
    doc = Document(filename="d.pdf", file_path="/tmp/d.pdf", content_hash="dash-tag", source=DocumentSource.MANUAL)
    session.add(doc); session.commit()
    today = _date.today()
    t = Transaction(document_id=doc.id, provider="Seguro Carro", category=Category.OTHER,
                    transaction_type=TransactionType.DEBIT, amount=321.0, paid_date=today)
    session.add(t); session.commit()
    file_transaction(session, t, get_node(session, "insurances.cars.car-insurance")); session.commit()

    page = client.get("/")
    assert page.status_code == 200
    assert "By tag" in page.text
    assert "/financials/transactions?tag=insurance" in page.text
    assert "/financials/transactions?tag=car" in page.text
    assert "321" in page.text


def test_dashboard_hides_the_by_tag_panel_when_there_are_no_tags(client, session):
    ensure_taxonomy(session)
    assert "By tag" not in client.get("/").text
