import re
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
    response = client.get("/overview/period", params={"range": "6m"})

    assert response.status_code == 200
    # The partial re-render must not include the full page chrome.
    assert "<html" not in response.text
    assert "Cash flow" in response.text
    assert "Where it went" in response.text


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
    assert 'href="/financials/transactions?transaction_type=credit' in response.text
    assert 'href="/financials/transactions?transaction_type=debit' in response.text


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

    full_year = client.get("/overview/period", params={"range": "ytd"})
    twelve_months = client.get("/overview/period", params={"range": "12m"})

    assert full_year.status_code == 200
    assert twelve_months.status_code == 200
    assert full_year.text != twelve_months.text


def test_overview_shows_a_card_per_domain(client, session):
    response = client.get("/")

    assert response.status_code == 200
    assert 'class="domain-card"' in response.text
    assert "Spent this month" in response.text
    assert "documents on file" in response.text
    assert 'href="/house"' in response.text


# ---- Overview "Budget" section -------------------------------------------------

def _file_spend(session, slug, amount, paid):
    from app.services.taxonomy import file_transaction, get_node

    doc = Document(filename=f"b-{slug}-{amount}.pdf", file_path="/tmp/b.pdf",
                   content_hash=f"h-{slug}-{amount}-{paid}", source=DocumentSource.MANUAL)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    t = Transaction(document_id=doc.id, provider="X", transaction_type=TransactionType.DEBIT,
                    amount=amount, currency="EUR", paid_date=paid)
    session.add(t)
    session.flush()
    file_transaction(session, t, get_node(session, slug))
    session.commit()


def test_overview_shows_budget_section_with_spend_and_status(client, session):
    from app.services.budget_service import set_budget
    from app.services.taxonomy import ensure_taxonomy, get_node

    ensure_taxonomy(session)
    today = date.today()
    _file_spend(session, "food.groceries.supermarket", 250.0, today)
    set_budget(session, get_node(session, "food.groceries.supermarket").id, today.year, 200.0)

    response = client.get("/")

    assert response.status_code == 200
    assert 'id="budget-panel"' in response.text
    assert "Budget" in response.text
    assert "Supermarket" in response.text
    assert "bud-over" in response.text  # 250 spent against 200 budgeted
    assert "Month-end estimate" in response.text


def test_budget_partial_route_month_and_year(client, session):
    from app.services.taxonomy import ensure_taxonomy

    ensure_taxonomy(session)
    for view, label in (("month", "Month-end estimate"), ("year", "Year-end estimate")):
        response = client.get("/overview/budget", params={"view": view})
        assert response.status_code == 200
        assert "<html" not in response.text
        assert 'id="budget-panel"' in response.text
        assert label in response.text
    assert client.get("/overview/budget", params={"view": "decade"}).status_code == 400


def test_post_budget_saves_and_returns_panel(client, session):
    from app.services.budget_service import get_budgets
    from app.services.taxonomy import ensure_taxonomy, get_node

    ensure_taxonomy(session)
    today = date.today()
    _file_spend(session, "food.groceries.supermarket", 40.0, today)
    node_id = get_node(session, "food.groceries.supermarket").id

    response = client.post(f"/overview/budget/{node_id}", data={"amount": "300", "view": "month"})

    assert response.status_code == 200
    assert 'id="budget-panel"' in response.text
    assert "<html" not in response.text
    assert re.search(r'bud-set[^"]*"[^>]*>\s*€300\s*<', response.text)
    session.expire_all()
    assert get_budgets(session, today.year)[node_id].amount == 300

    # a yearly line takes an expected month
    imi_id = get_node(session, "taxes-financial-costs.taxes.imi-property-tax").id
    response = client.post(f"/overview/budget/{imi_id}", data={"amount": "420", "expected_month": "10", "view": "year"})
    assert response.status_code == 200
    session.expire_all()
    assert get_budgets(session, today.year)[imi_id].expected_month == 10


def test_post_budget_rejects_bad_input(client, session):
    from app.services.taxonomy import ensure_taxonomy, get_node

    ensure_taxonomy(session)
    inflow = get_node(session, "income.psi.sessions").id
    yearly = get_node(session, "taxes-financial-costs.taxes.imi-property-tax").id
    monthly = get_node(session, "food.groceries.supermarket").id

    assert client.post(f"/overview/budget/{inflow}", data={"amount": "100"}).status_code == 400
    assert client.post(f"/overview/budget/{yearly}", data={"amount": "100", "expected_month": "13"}).status_code == 400
    r = client.post(f"/overview/budget/{monthly}", data={"amount": "abc"})
    assert r.status_code == 400
    assert "Traceback" not in r.text
    assert client.post(f"/overview/budget/{monthly}", data={"amount": "10", "view": "decade"}).status_code == 400
    assert client.post("/overview/budget/999999", data={"amount": "10"}).status_code == 400
    assert client.post(f"/overview/budget/{monthly}", data={}).status_code == 400


def test_overview_still_renders_with_no_taxonomy_data(client):
    response = client.get("/")

    assert response.status_code == 200
    assert 'id="budget-panel"' in response.text
    assert "No spending recorded yet." in response.text


def _file_credit(session, slug, amount, paid):
    from app.models.document import Document, DocumentSource
    from app.models.transaction import Transaction, TransactionType
    from app.services.taxonomy import file_transaction, get_node

    doc = session.exec(__import__("sqlmodel").select(Document)).first()
    if doc is None:
        doc = Document(filename="c.pdf", file_path="/tmp/c.pdf", content_hash="h-c", source=DocumentSource.MANUAL)
        session.add(doc)
        session.commit()
        session.refresh(doc)
    t = Transaction(document_id=doc.id, provider="X", transaction_type=TransactionType.CREDIT,
                    amount=amount, currency="EUR", paid_date=paid)
    session.add(t)
    session.flush()
    file_transaction(session, t, get_node(session, slug))
    session.commit()


def test_panel_shows_not_filed_captions_when_unfiled_data(client, session):
    from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy

    ensure_taxonomy(session)
    today = date.today()
    _file_spend(session, UNSORTED_SLUG, 60.0, today)
    _file_credit(session, UNSORTED_SLUG, 900.0, today)
    text = client.get("/overview/budget").text
    assert "+ €900 income not filed yet" in text
    assert "+ €60 not filed yet" in text
    assert "not filed yet: €900 income, €60 spending" in text
    assert "before loan repayments" in text
    assert "/financials/transactions/needs-review" in text


def test_panel_omits_not_filed_captions_without_unfiled_data(client, session):
    from app.services.taxonomy import ensure_taxonomy

    ensure_taxonomy(session)
    _file_spend(session, "food.groceries.supermarket", 10.0, date.today())
    text = client.get("/overview/budget").text
    assert "not filed" not in text
    assert "before loan repayments" in text


def test_panel_hints_and_error_slot(client, session):
    from app.services.budget_service import set_budget
    from app.services.taxonomy import ensure_taxonomy, get_node

    ensure_taxonomy(session)
    today = date.today()
    _file_spend(session, "food.groceries.supermarket", 10.0, today)
    month = client.get("/overview/budget", params={"view": "month"}).text
    assert "No budgets yet" in month
    assert "Yearly items without a due month show in the Year view" in month
    assert "Yearly items without a due month" not in client.get("/overview/budget", params={"view": "year"}).text
    assert "bud-error" in month and "htmx:responseError" in month
    # a budget on a hidden yearly line still counts as "a budget exists"
    other = 12 if today.month != 12 else 1
    set_budget(session, get_node(session, "taxes-financial-costs.taxes.imi-property-tax").id,
               today.year, 420.0, expected_month=other)
    assert "No budgets yet" not in client.get("/overview/budget").text


def test_carried_over_label_is_shown(client, session):
    from app.models.budget import Budget
    from app.services.taxonomy import ensure_taxonomy, get_node

    ensure_taxonomy(session)
    node = get_node(session, "food.groceries.supermarket")
    session.add(Budget(node_id=node.id, year=date.today().year - 1, amount=200.0))
    session.commit()
    assert "carried over" in client.get("/overview/budget").text


def test_budget_400_body_is_plain_text(client, session):
    from app.services.taxonomy import ensure_taxonomy, get_node

    ensure_taxonomy(session)
    monthly = get_node(session, "food.groceries.supermarket").id
    r = client.post(f"/overview/budget/{monthly}", data={"amount": "abc"})
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == "amount must be a number"


def _loan_for_panel(session, due):
    from decimal import Decimal
    from app.models.debt import Debt, DebtDirection, DebtKind
    from app.models.position import LoanSnapshot
    from app.services.taxonomy import ensure_taxonomy
    ensure_taxonomy(session)
    doc = Document(filename="l.pdf", file_path="/tmp/l.pdf", content_hash="h-panel-loan", source=DocumentSource.MANUAL)
    session.add(doc)
    debt = Debt(kind=DebtKind.FORMAL, direction=DebtDirection.OWED_BY_US, original_amount=1000.0,
                current_balance=Decimal("0"), name="Loan", external_number="9000009", status="active")
    session.add(debt)
    session.commit()
    session.add(LoanSnapshot(debt_id=debt.id, as_of=date.today(), capital_remaining=50000.0, rate_percent=3.0,
                             next_due_date=due, next_instalment=500.0, document_id=doc.id))
    session.commit()


def test_budget_panel_shows_loan_instalments_and_caption(client, session):
    _loan_for_panel(session, date.today())
    html = client.get("/overview/budget?view=month").text
    assert "loan instalments" in html
    assert 'href="/financials/loans"' in html
    assert "after loan instalments" in html
    assert "before loan repayments" not in html


def test_budget_panel_without_loans_keeps_old_caption(client, session):
    html = client.get("/overview/budget?view=month").text
    assert "before loan repayments" in html
    assert "loan instalments" not in html
