"""The baseline (app/baseline.py): records dated before 10 Oct 2025 are kept but shown and counted nowhere;
the opening position on the baseline day starts the Cash card."""
from datetime import date

import pytest
from sqlmodel import select

from app.baseline import BASELINE_DATE, baseline_note
from app.domains.financials.ask import FINANCIALS_ASK_TOOLS
from app.models.account import Account, AccountType
from app.models.domain import Domain
from app.models.transaction import Transaction, TransactionType
from app.services.classification_engine import unsorted_transactions_query
from app.services.overview_service import get_cash_kpi
from tests.knowledge_factories import make_document


@pytest.fixture()
def doc(session):
    return make_document(session, domain=Domain.FINANCIALS, category="statement", filename="stmt.pdf")


def _txn(session, doc, paid, amount=10.0, **kw):
    kw.setdefault("description", "Padaria Sol")
    t = Transaction(document_id=doc.id, amount=amount, paid_date=paid, transaction_type=TransactionType.DEBIT, **kw)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def test_the_baseline_day_is_10_oct_2025():
    assert BASELINE_DATE == date(2025, 10, 10)


def test_counts_keeps_the_baseline_day_and_drops_the_day_before(session, doc):
    old = _txn(session, doc, date(2025, 10, 9))
    first = _txn(session, doc, date(2025, 10, 10))
    ids = set(session.exec(select(Transaction.id).where(Transaction.counts())).all())
    assert first.id in ids and old.id not in ids


def test_a_bill_without_a_paid_date_is_dated_by_its_due_date(session, doc):
    old = _txn(session, doc, None, due_date=date(2025, 3, 1))
    new = _txn(session, doc, None, due_date=date(2026, 3, 1))
    undated = _txn(session, doc, None)
    ids = set(session.exec(select(Transaction.id).where(Transaction.counts())).all())
    assert new.id in ids and undated.id in ids and old.id not in ids


def test_old_rows_do_not_reach_the_needs_review_queue(session, doc):
    old = _txn(session, doc, date(2024, 5, 1))
    new = _txn(session, doc, date(2026, 5, 1))
    queued = {t.id for t in session.exec(unsorted_transactions_query()).all()}
    assert new.id in queued and old.id not in queued


def test_old_rows_do_not_reach_ask(session, doc):
    _txn(session, doc, date(2024, 5, 1), amount=1000.0, category="groceries")
    _txn(session, doc, date(2026, 5, 1), amount=1.0, category="groceries")
    tool = next(t for t in FINANCIALS_ASK_TOOLS if t.name == "financials_spending")
    out = tool.run(session, {"date_from": "2024-01-01", "date_to": "2026-12-31"})
    assert "total €1.00" in out.text


def test_the_transactions_page_lists_only_rows_from_the_baseline_on(client, session, doc):
    _txn(session, doc, date(2024, 5, 1), description="OLD-ROW-MARK")
    _txn(session, doc, date(2026, 5, 1), description="NEW-ROW-MARK")
    html = client.get("/financials/transactions").text
    assert "NEW-ROW-MARK" in html and "OLD-ROW-MARK" not in html
    assert "Statement records go back to 2024-05-01" in html


def test_cash_card_shows_the_balance_the_bank_reports(session, doc):
    from app.models.bank import BankAccountLink, BankConnection
    conn = BankConnection(bank_name="Santander Totta", country="PT", state="st-cash")
    account = Account(name="Current", institution="Test Bank", account_type=AccountType.CHECKING)
    session.add(conn)
    session.add(account)
    session.commit()
    session.refresh(conn)
    session.refresh(account)
    session.add(BankAccountLink(connection_id=conn.id, bank_account_uid="u1", account_id=account.id,
                                balance=62.81, balance_as_of=date(2026, 10, 8)))
    session.add(BankAccountLink(connection_id=conn.id, bank_account_uid="u0", account_id=account.id,
                                balance=999.0, balance_as_of=date(2026, 9, 1)))   # an older reading of the same account
    session.commit()
    _txn(session, doc, date(2026, 5, 1), amount=30.0, account_id=account.id)
    card = get_cash_kpi(session, date(2026, 10, 15))
    assert card.value == 62.81 and "2026-10-08" in card.caption


def test_cash_card_without_a_bank_balance_still_shows_the_tracked_flow(session, doc):
    account = Account(name="Current", institution="Test Bank", account_type=AccountType.CHECKING)
    session.add(account)
    session.commit()
    session.refresh(account)
    _txn(session, doc, date(2026, 5, 1), amount=30.0, account_id=account.id)
    assert get_cash_kpi(session, date(2026, 10, 15)).value == -30.0


def test_the_note_is_empty_when_nothing_is_older(session, doc):
    _txn(session, doc, date(2026, 5, 1))
    assert baseline_note(session) == ""
