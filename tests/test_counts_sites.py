"""Every figure, chart, list and Ask answer that adds up the family's money counts exactly the rows
that count (decision D17): rows nothing flags, and rows a person confirmed. A line waiting for a check
("needs_check"), a line a person rejected, and a bill's record already settled by a bank row count
nowhere.

Each test builds the same five rows with the REAL models, one per kind, with amounts that are powers of
ten (1, 10, 100, 1000, 10000), so a wrong figure shows which kind leaked in: counting 1 + confirmed 10
= 11 is the only right answer. The figure is then asked of the real service or page that produces it.
See docs/privacy/counts-inventory.md for the list of sites these tests cover."""
import re
from datetime import date

import pytest
from sqlmodel import select

from app.domains.financials.ask import FINANCIALS_ASK_TOOLS
from app.domains.financials.overview import financials_overview_card
from app.models.account import Account, AccountType
from app.models.commitment import Cadence, Commitment
from app.models.domain import Domain
from app.models.transaction import Category, Transaction, TransactionType
from app.services.budget_service import get_budget_overview
from app.services.overview_service import get_cash_kpi, get_category_comparison, get_overview_data
from app.services.tag_service import ensure_tags, tag_totals
from app.services.taxonomy import ensure_taxonomy, file_transaction, get_node
from tests.knowledge_factories import make_document

# kind -> (amount, review_status). "settled" is also given settled_by_id in five().
KINDS = {
    "counting": (1.0, None),
    "confirmed": (10.0, "confirmed"),
    "held": (100.0, "needs_check"),
    "rejected": (1000.0, "rejected"),
    "settled": (10000.0, None),
}
RIGHT = 11.0  # counting + confirmed


@pytest.fixture()
def doc(session):
    return make_document(session, domain=Domain.FINANCIALS, category="statement", filename="stmt.pdf")


def five(session, doc, *, scale=1.0, post=None, **common):
    """One row of each kind. `post(row)` runs on every row once saved (filing under a node, linking...)."""
    common.setdefault("description", "Padaria Sol")
    common.setdefault("paid_date", date(2026, 10, 5))
    common.setdefault("transaction_type", TransactionType.DEBIT)
    rows = {}
    for kind, (amount, status) in KINDS.items():
        t = Transaction(document_id=doc.id, amount=amount * scale, review_status=status, **common)
        session.add(t)
        session.commit()
        session.refresh(t)
        rows[kind] = t
    rows["settled"].settled_by_id = rows["counting"].id
    session.add(rows["settled"])
    session.commit()
    if post:
        for t in rows.values():
            post(t)
            session.commit()
    return rows


def _tool(name):
    return next(t for t in FINANCIALS_ASK_TOOLS if t.name == name)


# ---- Ask ----------------------------------------------------------------------------------------

def test_ask_spending_total_counts_only_rows_that_count(session, doc):
    five(session, doc, category=Category.GROCERIES)
    out = _tool("financials_spending").run(session, {"date_from": "2026-10-01", "date_to": "2026-10-31"})
    assert "total €11.00" in out.text
    assert "groceries: €11.00 (2 payments)" in out.text


def test_ask_find_transactions_lists_only_rows_that_count(session, doc):
    rows = five(session, doc)
    out = _tool("financials_find_transactions").run(session, {"query": "padaria"})
    assert out.text.startswith("2 matching transactions")
    listed = {c.ref for c in out.citables}
    assert listed == {f"financials:txn:{rows['counting'].id}", f"financials:txn:{rows['confirmed'].id}"}
    for kind in ("held", "rejected", "settled"):
        assert f"financials:txn:{rows[kind].id}" not in out.text


# ---- Overview: the Financials card, the KPIs, the tables ----------------------------------------

def test_financials_card_spent_this_month(session, doc):
    five(session, doc, paid_date=date(2026, 10, 5))
    card = financials_overview_card(session, date(2026, 10, 15))
    assert card.lines[0].text == "Spent this month: €11.00"


def test_overview_income_expenses_and_net_flow(session, doc):
    five(session, doc, scale=1.0, transaction_type=TransactionType.DEBIT)
    five(session, doc, scale=2.0, transaction_type=TransactionType.CREDIT)
    kpis = {k.label: k.value for k in get_overview_data(session, date(2026, 10, 15)).flow_kpis}
    assert kpis["Expenses"] == RIGHT
    assert kpis["Income"] == RIGHT * 2
    assert kpis["Net flow"] == RIGHT


def test_overview_cash_balance(session, doc):
    account = Account(name="Current", institution="Test Bank", account_type=AccountType.CHECKING)
    session.add(account)
    session.commit()
    session.refresh(account)
    five(session, doc, account_id=account.id)  # five debits
    assert get_cash_kpi(session, date(2026, 10, 15)).value == -RIGHT


def test_overview_yearly_commitments_actual_total(session, doc):
    commitment = Commitment(name="IMI", cadence=Cadence.YEARLY, planned_amount=500.0, year=2026)
    session.add(commitment)
    session.commit()
    session.refresh(commitment)
    five(session, doc, commitment_id=commitment.id)
    card = get_overview_data(session, date(2026, 10, 15)).yearly_commitments
    assert card.actual_total == RIGHT


def test_overview_where_it_went_category_comparison(session, doc):
    five(session, doc, category=Category.GROCERIES, paid_date=date(2026, 10, 5))
    rows = get_category_comparison(session, date(2026, 10, 15))
    assert {r.category: r.current_value for r in rows} == {"groceries": RIGHT}


def test_overview_page_shows_the_right_figures(client, session, doc):
    """Through the page, with today's date: the Financials card and the Expenses card."""
    five(session, doc, scale=100.0, paid_date=date.today(), category=Category.GROCERIES)
    html = client.get("/").text
    assert "Spent this month: €1,100.00" in html
    expenses = re.search(r"Expenses.*?fc-card-val\">€([\d,]+)<", html, re.S)
    assert expenses and expenses.group(1) == "1,100"


# ---- Tags ---------------------------------------------------------------------------------------

def test_tag_totals_count_only_rows_that_count(session, doc):
    ensure_taxonomy(session)
    ensure_tags(session)
    node = get_node(session, "insurances.cars.car-insurance")
    five(session, doc, post=lambda t: file_transaction(session, t, node), paid_date=date(2026, 10, 5))
    total = next(t for t in tag_totals(session, date(2026, 10, 8)) if t.name == "insurance")
    assert total.month_to_date == RIGHT
    assert total.year_to_date == RIGHT
    assert total.transaction_count_ytd == 2


# ---- Budgets and forecast -----------------------------------------------------------------------

def test_budget_line_spent_counts_only_rows_that_count(session, doc):
    ensure_taxonomy(session)
    node = get_node(session, "food.groceries.supermarket")
    five(session, doc, post=lambda t: file_transaction(session, t, node), paid_date=date(2026, 10, 5))
    overview = get_budget_overview(session, date(2026, 10, 15))
    lines = {l.name: l for g in overview.groups for c in g.categories for l in c.lines}
    assert lines["Supermarket"].spent == RIGHT
    year = get_budget_overview(session, date(2026, 10, 15), "year")
    assert {l.name: l for g in year.groups for c in g.categories for l in c.lines}["Supermarket"].spent == RIGHT


def test_budget_unsorted_outflow_counts_only_rows_that_count(session, doc):
    ensure_taxonomy(session)
    five(session, doc, paid_date=date(2026, 10, 5))  # not filed under any node: the "Unsorted" bucket
    overview = get_budget_overview(session, date(2026, 10, 15))
    assert overview.unsorted_spent == RIGHT


def test_budget_loan_instalments_paid_counts_only_rows_that_count(session, doc):
    from tests.test_loans_router import _seed
    loan = _seed(session)
    five(session, doc, debt_id=loan.id, paid_date=date(2026, 10, 5))
    assert get_budget_overview(session, date(2026, 10, 15), "month").loan_instalments_paid == RIGHT
    assert get_budget_overview(session, date(2026, 10, 15), "year").loan_instalments_paid == RIGHT


# ---- Loans page: insurance paid -----------------------------------------------------------------

def test_loan_page_insurance_paid_counts_only_rows_that_count(client, session, doc):
    from app.services.loan_insurance import LIFE_SLUG
    from tests.test_loans_router import _seed

    ensure_taxonomy(session)
    loan = _seed(session)
    life = get_node(session, LIFE_SLUG)

    def link(t):  # what the loan-insurance linker ends with: the loan, and the Loan insurance node
        t.debt_id = loan.id
        file_transaction(session, t, life)

    five(session, doc, scale=100.0, description="SEG VIDA 15.000001-2026/08/21", post=link)
    html = client.get(f"/financials/loans/{loan.id}").text
    assert "€1,100.00" in html  # insurance paid: counting 100 + confirmed 1000
    assert "last premium" in html.lower() and "€1,000.00" in html  # the last premium is the confirmed one
