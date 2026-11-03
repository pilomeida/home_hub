from datetime import date

from app.domains.financials.ask import FINANCIALS_ASK_TOOLS
from app.domains.registry import get_spec
from app.models.domain import Domain
from app.models.transaction import Transaction, TransactionType
from app.services.taxonomy import ensure_taxonomy, file_transaction, get_node
from tests.knowledge_factories import make_document


def _tool(name):
    return next(t for t in FINANCIALS_ASK_TOOLS if t.name == name)


def _txn(session, doc, provider, leaf, amount, paid, ttype=TransactionType.DEBIT):
    """A line filed on its spending-tree leaf (slug), or not filed yet (None)."""
    t = Transaction(document_id=doc.id, description=provider, amount=amount, currency="EUR", paid_date=paid,
                    transaction_type=ttype)
    session.add(t)
    session.commit()
    if leaf is not None:
        ensure_taxonomy(session)
        file_transaction(session, t, get_node(session, leaf))
        session.commit()
    return t


def test_spending_by_category_counts_spending_in_range_on_the_tree(session):
    doc = make_document(session, domain=Domain.FINANCIALS, category="statement", filename="stmt.pdf")
    _txn(session, doc, "EDP", "housing.utilities.electricity", 80.0, date(2025, 11, 3))
    _txn(session, doc, "EDP", "housing.utilities.electricity", 20.0, date(2025, 12, 1))
    _txn(session, doc, "Salary", "income.employment.salary", 3000.0, date(2025, 12, 1), TransactionType.CREDIT)
    _txn(session, doc, "EDP", "housing.utilities.electricity", 999.0, date(2025, 10, 20))
    out = _tool("financials_spending").run(session, {"date_from": "2025-11-01", "date_to": "2025-12-31"})
    assert "Housing › Utilities: €100.00 (2 payments)" in out.text
    assert "Income" not in out.text and "999" not in out.text
    assert out.citables[0].url.startswith("/financials/transactions?")
    assert out.used_raw_sources is True


def test_spending_counts_as_the_budget_does(session):
    """A refund under the same leaf takes off; a line not filed yet counts as Unsorted; a transfer never counts."""
    doc = make_document(session, domain=Domain.FINANCIALS, category="statement", filename="stmt.pdf")
    _txn(session, doc, "CONTINENTE", "food.groceries.supermarket", 100.0, date(2025, 11, 3))
    _txn(session, doc, "CONTINENTE", "food.groceries.supermarket", 30.0, date(2025, 11, 4), TransactionType.CREDIT)
    _txn(session, doc, "MYSTERY", None, 12.0, date(2025, 11, 5))
    _txn(session, doc, "TO REVOLUT", "internal-transfers.between-my-accounts.santander-revolut", 500.0,
         date(2025, 11, 6), TransactionType.TRANSFER)
    out = _tool("financials_spending").run(session, {"date_from": "2025-11-01", "date_to": "2025-11-30"})
    assert "Food › Groceries: €70.00" in out.text and "Unsorted: €12.00" in out.text
    assert "total €82.00" in out.text and "500" not in out.text


def test_spending_filters_by_a_group_or_category_of_the_tree(session):
    doc = make_document(session, domain=Domain.FINANCIALS, category="statement", filename="stmt.pdf")
    _txn(session, doc, "CONTINENTE", "food.groceries.supermarket", 40.0, date(2025, 11, 3))
    _txn(session, doc, "TASCA", "food.eat-out.restaurants", 25.0, date(2025, 11, 4))
    _txn(session, doc, "EDP", "housing.utilities.electricity", 60.0, date(2025, 11, 5))
    run = _tool("financials_spending").run
    food = run(session, {"date_from": "2025-11-01", "date_to": "2025-11-30", "category": "food"})
    assert "total €65.00" in food.text and "Utilities" not in food.text
    assert "category_node=food" in food.citables[0].url
    groceries = run(session, {"date_from": "2025-11-01", "date_to": "2025-11-30", "category": "food.groceries"})
    assert "total €40.00" in groceries.text
    unknown = run(session, {"date_from": "2025-11-01", "date_to": "2025-11-30", "category": "groceries"})
    assert "There is no spending category 'groceries'" in unknown.text and "food.groceries" in unknown.text


def test_the_tool_offers_the_trees_spending_groups_and_categories():
    schema = _tool("financials_spending").input_schema["properties"]["category"]["enum"]
    assert {"food", "food.groceries", "housing.utilities", "insurances"} <= set(schema)
    assert "income" not in schema and "electricity" not in schema


def test_find_transactions_by_provider(session):
    doc = make_document(session, domain=Domain.FINANCIALS, category="statement", filename="stmt.pdf")
    _txn(session, doc, "AT - IMI 2026 1a prestacao", "taxes-financial-costs.taxes.imi-property-tax", 150.0,
         date(2026, 5, 10))
    out = _tool("financials_find_transactions").run(session, {"query": "imi"})
    assert "IMI" in out.text and "Taxes & financial costs › Taxes" in out.text
    assert out.citables[0].ref.startswith("financials:txn:")


def test_registered_on_financials_spec():
    assert {t.name for t in get_spec(Domain.FINANCIALS).ask_tools} == {"financials_spending", "financials_find_transactions"}
