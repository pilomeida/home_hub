"""PLAN step 6c (C7): the same spending question gives the same number on Home and the Overview, in Ask and in the
budget. One rule (`spend_of`, `earning_of`); loan instalments and moves between the family's own accounts are named
under Spending, never silently dropped or counted."""

from datetime import date

from app.categories.financials.ask import FINANCIALS_ASK_TOOLS
from app.models.category import Category
from app.models.transaction import Transaction, TransactionType
from app.services.budget_service import get_budget_overview
from app.services.home import money_this_month
from app.services.overview_service import _monthly_flow_totals
from app.services.taxonomy import ensure_taxonomy, file_transaction, get_node
from tests.knowledge_factories import make_document

TODAY = date(2026, 11, 20)


def _txn(session, doc, name, leaf, amount, paid, ttype=TransactionType.DEBIT):
    t = Transaction(document_id=doc.id, description=name, amount=amount, currency="EUR", paid_date=paid,
                    transaction_type=ttype)
    session.add(t)
    session.commit()
    if leaf is not None:
        ensure_taxonomy(session)
        file_transaction(session, t, get_node(session, leaf))
        session.commit()
    return t


def _month(session):
    doc = make_document(session, category=Category.FINANCIALS, paper_kind="statement", filename="stmt.pdf")
    _txn(session, doc, "CONTINENTE", "food.groceries.supermarket", 120.0, date(2026, 11, 3))
    _txn(session, doc, "CONTINENTE", "food.groceries.supermarket", 20.0, date(2026, 11, 4), TransactionType.CREDIT)
    _txn(session, doc, "MYSTERY", None, 15.0, date(2026, 11, 5))
    _txn(session, doc, "PRESTACAO HABITACAO", "loans-debt.loan-repayments.mortgage", 900.0, date(2026, 11, 6))
    _txn(session, doc, "TO REVOLUT", "internal-transfers.between-my-accounts.santander-revolut", 300.0,
         date(2026, 11, 7))
    _txn(session, doc, "FROM REVOLUT", "internal-transfers.between-my-accounts.santander-revolut", 50.0,
         date(2026, 11, 8), TransactionType.CREDIT)
    _txn(session, doc, "SALARIO", "income.employment.salary", 3000.0, date(2026, 11, 1), TransactionType.CREDIT)


def test_home_ask_and_the_budget_give_the_same_spending(session):
    _month(session)
    earnings, spending = money_this_month(session, TODAY)
    ask = next(t for t in FINANCIALS_ASK_TOOLS if t.name == "financials_spending").run(
        session, {"date_from": "2026-11-01", "date_to": "2026-11-20"})
    budget = get_budget_overview(session, TODAY, "month")
    assert spending.now == 115.0                                   # 120 − 20 refund + 15 not filed yet
    assert "total €115.00" in ask.text
    assert round(budget.spend_spent + budget.unsorted_spent, 2) == 115.0
    assert _monthly_flow_totals(session, TODAY)[1]["2026-11"] == 115.0     # the Overview's chart


def test_earnings_leave_out_moves_between_own_accounts(session):
    _month(session)
    earnings, _ = money_this_month(session, TODAY)
    assert earnings.now == 3000.0


def test_money_out_that_is_not_spending_is_named_on_home(client, session):
    _month(session)
    _, spending = money_this_month(session, TODAY)
    assert spending.left_out == [("loan instalments", 900.0), ("moves between your own accounts", 300.0)]
    html = client.get("/").text
    assert "Not counted as spending this month: loan instalments" in html
