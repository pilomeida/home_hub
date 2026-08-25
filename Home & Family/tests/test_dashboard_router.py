from datetime import date

from app.models.account import Account, AccountType
from app.models.commitment import Cadence, Commitment
from app.models.document import Document, DocumentSource
from app.models.todo import Todo
from app.models.transaction import Category, Transaction, TransactionType


def test_dashboard_renders_open_todos(client, session):
    session.add(Todo(title="Pay EDP", due_date=date(2026, 9, 5)))
    session.commit()

    response = client.get("/")

    assert response.status_code == 200
    assert "Pay EDP" in response.text


def test_cash_flow_chart_partial_route_responds(client, session):
    response = client.get("/cash-flow-chart", params={"range": "6m"})

    assert response.status_code == 200
    # The partial re-render must not include the full page chrome.
    assert "<html" not in response.text


def test_overview_page_renders_kpi_cards_and_sections(client, session):
    account = Account(name="Santander", institution="Santander", account_type=AccountType.CHECKING)
    session.add(account)
    session.commit()
    session.refresh(account)

    document = Document(
        filename="s.pdf", file_path="/tmp/s.pdf", content_hash="hh",
        source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    t = Transaction(
        document_id=document.id, provider="SALARIO", category=Category.INCOME,
        transaction_type=TransactionType.CREDIT, amount=2000.0, currency="EUR",
        account_id=account.id, paid_date=date.today(),
    )
    session.add(t)
    session.commit()

    response = client.get("/")

    assert response.status_code == 200
    assert "Income" in response.text
    assert "Expenses" in response.text
    assert "Net flow" in response.text
    assert "Cash" in response.text
    assert "Debt" in response.text
    assert "Yearly" in response.text
    assert "Needs attention" in response.text
    assert "Household" in response.text


def test_overview_page_kpi_drill_down_links_present(client, session):
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/transactions?transaction_type=credit' in response.text
    assert 'href="/transactions?transaction_type=debit' in response.text


def test_overview_page_handles_yearly_commitment_with_zero_planned_amount(client, session):
    # Regression test: a yearly Commitment can exist before its budget is filled
    # in, i.e. planned_amount == 0. get_yearly_commitments_card() correctly sets
    # pct_of_plan = None in that case (avoiding a division by zero), but with
    # has_commitments True the template must still render without crashing.
    session.add(
        Commitment(name="X", cadence=Cadence.YEARLY, planned_amount=0.0, year=date.today().year)
    )
    session.commit()

    response = client.get("/")

    assert response.status_code == 200


def test_overview_page_renders_cleanly_with_empty_database(client, session):
    response = client.get("/")

    assert response.status_code == 200
    assert "No yearly commitments configured yet." in response.text
    assert "No history tracked yet" in response.text  # Debt KPI, no Debt rows
    assert "No category spend recorded yet." in response.text
    assert "All clear." in response.text


def test_cash_flow_range_pill_swap_changes_content(client, session):
    document = Document(
        filename="s.pdf", file_path="/tmp/s.pdf", content_hash="hh2", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    session.add(Transaction(
        document_id=document.id, provider="SALARIO", category=Category.INCOME,
        transaction_type=TransactionType.CREDIT, amount=2000.0, currency="EUR",
        paid_date=date(2025, 1, 15),
    ))
    session.commit()

    full_year = client.get("/cash-flow-chart", params={"range": "ytd"})
    twelve_months = client.get("/cash-flow-chart", params={"range": "12m"})

    assert full_year.status_code == 200
    assert twelve_months.status_code == 200
    assert full_year.text != twelve_months.text
