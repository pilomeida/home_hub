from datetime import date

from app.domains.financials.ask import FINANCIALS_ASK_TOOLS
from app.domains.registry import get_spec
from app.models.domain import Domain
from app.models.transaction import Category, Transaction, TransactionType
from tests.knowledge_factories import make_document


def _tool(name):
    return next(t for t in FINANCIALS_ASK_TOOLS if t.name == name)


def _txn(session, doc, provider, category, amount, paid, ttype=TransactionType.DEBIT):
    session.add(Transaction(document_id=doc.id, provider=provider, category=category, amount=amount,
                            currency="EUR", paid_date=paid, transaction_type=ttype))
    session.commit()


def test_spending_by_category_debits_only_in_range(session):
    doc = make_document(session, domain=Domain.FINANCIALS, category="statement", filename="stmt.pdf")
    _txn(session, doc, "EDP", Category.ELECTRICITY, 80.0, date(2025, 3, 1))
    _txn(session, doc, "EDP", Category.ELECTRICITY, 20.0, date(2025, 4, 1))
    _txn(session, doc, "Salary", Category.INCOME, 3000.0, date(2025, 4, 1), TransactionType.CREDIT)
    _txn(session, doc, "EDP", Category.ELECTRICITY, 999.0, date(2024, 12, 31))
    out = _tool("financials_spending").run(session, {"date_from": "2025-01-01", "date_to": "2025-12-31"})
    assert "electricity: €100.00 (2 payments)" in out.text
    assert "income" not in out.text and "999" not in out.text
    assert out.citables[0].url.startswith("/financials/transactions?") and "transaction_type=debit" in out.citables[0].url
    assert out.used_raw_sources is True


def test_find_transactions_by_provider(session):
    doc = make_document(session, domain=Domain.FINANCIALS, category="statement", filename="stmt.pdf")
    _txn(session, doc, "AT - IMI 2026 1a prestacao", Category.OTHER_EXPENSE, 150.0, date(2026, 5, 10))
    out = _tool("financials_find_transactions").run(session, {"query": "imi"})
    assert "IMI" in out.text and out.citables[0].ref.startswith("financials:txn:")


def test_registered_on_financials_spec():
    assert {t.name for t in get_spec(Domain.FINANCIALS).ask_tools} == {"financials_spending", "financials_find_transactions"}
