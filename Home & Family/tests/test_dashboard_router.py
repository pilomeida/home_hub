from datetime import date

from app.models.account import Account, AccountType
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
    assert "Needs Attention" in response.text
    assert "Household" in response.text


def test_overview_page_kpi_drill_down_links_present(client, session):
    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/transactions?category=income' in response.text
    assert 'href="/transactions?transaction_type=debit' in response.text
