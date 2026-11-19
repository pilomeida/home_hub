"""PLAN step 4a-b: a statement for days the bank feed already brought adds no double lines. Overlap shows as
overlap (the statement line is kept, settled by its feed line, out of every total); a line a person checked is
never changed."""

import asyncio
from datetime import date

from sqlmodel import func, select

from app.categories.financials import handler
from app.categories.financials.feed import feed_statement
from app.models.account import Account
from app.models.document import Document, DocumentSource
from app.models.transaction import Transaction, TransactionType
from app.services.extraction import ExtractedStatement, ExtractedTransaction
from app.services.feed_overlap import match_statement_to_feed


def _account(session):
    acct = Account(name="Conta Santander", institution="Santander")
    session.add(acct); session.commit()
    return acct


def _feed_line(session, acct, day, amount, desc, kind=TransactionType.DEBIT, ext=None):
    doc = feed_statement(session, acct.id, "Santander")
    t = Transaction(document_id=doc.id, description=desc, amount=amount, transaction_type=kind,
                    paid_date=date(2026, 10, day), account_id=acct.id, external_id=ext or f"ref-{day}-{amount}")
    session.add(t); session.commit()
    return t


def _line(day, amount, desc, kind="debit"):
    return ExtractedTransaction(date(2026, 10, day), desc, amount, "EUR", kind, "other")


def _read_statement(session, acct, monkeypatch, tmp_path, items):
    path = tmp_path / "extrato-2026-10.pdf"
    path.write_bytes(b"%PDF-1.4 x")
    doc = Document(filename=path.name, file_path=str(path), content_hash="h-st", source=DocumentSource.MANUAL,
                   paper_kind="statement", account_id=acct.id)
    session.add(doc); session.commit()

    async def extract(file_path):
        return ExtractedStatement("2026-10", items)

    async def classify(session, t, gateway=None):
        t.account_id = acct.id
        return type("R", (), {"created_new_merchant": False})()
    monkeypatch.setattr(handler, "extract_statement_transactions", extract)
    monkeypatch.setattr(handler, "classify_transaction", classify)
    asyncio.run(handler.read_statement(session, doc))
    return doc


def _counted(session):
    return session.exec(select(func.count()).select_from(Transaction).where(Transaction.counts())).one()


def test_a_statement_over_feed_days_adds_no_double_lines(session, monkeypatch, tmp_path):
    acct = _account(session)
    a = _feed_line(session, acct, 20, 45.10, "PINGO DOCE MAFRA")
    b = _feed_line(session, acct, 21, 12.00, "TRF MBWAY P/MATIAS")
    _feed_line(session, acct, 23, 800.0, "SALARIO", kind=TransactionType.CREDIT)
    before = _counted(session)
    doc = _read_statement(session, acct, monkeypatch, tmp_path, [
        _line(19, 45.10, "COMPRA 3315 PINGO DOCE"),        # booked a day earlier on paper: the same payment
        _line(21, 12.00, "TRF MBWAY P/MATIAS"),
        _line(5, 30.00, "COMPRA 3315 FARMACIA"),           # before the feed started: a new line
    ])
    lines = {t.description: t for t in session.exec(select(Transaction).where(Transaction.document_id == doc.id))}
    assert lines["COMPRA 3315 PINGO DOCE"].settled_by_id == a.id
    assert lines["TRF MBWAY P/MATIAS"].settled_by_id == b.id
    assert lines["COMPRA 3315 FARMACIA"].settled_by_id is None
    assert _counted(session) == before + 1                 # only the line the feed did not have counts


def test_one_feed_line_absorbs_one_statement_line_and_the_nearest_date_wins(session):
    acct = _account(session)
    near = _feed_line(session, acct, 10, 9.99, "NETFLIX")
    far = _feed_line(session, acct, 12, 9.99, "NETFLIX", ext="ref-x")
    doc = Document(filename="extrato.pdf", file_path="/tmp/e.pdf", content_hash="h-e", source=DocumentSource.MANUAL,
                   paper_kind="statement", account_id=acct.id)        # a paper statement, not the feed's own
    session.add(doc); session.commit()
    s1 = Transaction(document_id=doc.id, description="NETFLIX", amount=9.99, transaction_type=TransactionType.DEBIT,
                     paid_date=date(2026, 10, 11), account_id=acct.id)
    s2 = Transaction(document_id=doc.id, description="NETFLIX", amount=9.99, transaction_type=TransactionType.DEBIT,
                     paid_date=date(2026, 10, 11), account_id=acct.id)
    s3 = Transaction(document_id=doc.id, description="NETFLIX", amount=9.99, transaction_type=TransactionType.DEBIT,
                     paid_date=date(2026, 10, 11), account_id=acct.id)
    session.add_all([s1, s2, s3]); session.commit()
    report = match_statement_to_feed(session, [s1, s2, s3])
    assert report.overlapped == 2 and {s1.settled_by_id, s2.settled_by_id} == {near.id, far.id}
    assert s3.settled_by_id is None                        # a third payment of the same amount stays a payment


def test_a_line_a_person_checked_is_never_changed(session):
    acct = _account(session)
    _feed_line(session, acct, 10, 50.0, "EDP")
    doc = Document(filename="extrato2.pdf", file_path="/tmp/e2.pdf", content_hash="h-e2", source=DocumentSource.MANUAL,
                   paper_kind="statement", account_id=acct.id)
    session.add(doc); session.commit()
    checked = Transaction(document_id=doc.id, description="EDP", amount=50.0, transaction_type=TransactionType.DEBIT,
                          paid_date=date(2026, 10, 10), account_id=acct.id, checked_by="rute@example.com")
    session.add(checked); session.commit()
    assert match_statement_to_feed(session, [checked]).overlapped == 0 and checked.settled_by_id is None


def test_a_statement_line_the_feed_holds_does_not_take_a_bill_from_its_feed_twin(session, monkeypatch, tmp_path):
    from app.services.document_reconcile import reconcile_documents
    acct = _account(session)
    feed = _feed_line(session, acct, 20, 60.0, "EDP COMERCIAL")
    _read_statement(session, acct, monkeypatch, tmp_path, [_line(20, 60.0, "EDP COMERCIAL")])
    bill_doc = Document(filename="edp.pdf", file_path="/tmp/edp.pdf", content_hash="h-bill",
                        source=DocumentSource.MANUAL, paper_kind="bill")
    session.add(bill_doc); session.commit()
    bill = Transaction(document_id=bill_doc.id, description="EDP COMERCIAL", amount=60.0,
                       transaction_type=TransactionType.DEBIT, paid_date=date(2026, 10, 19))
    session.add(bill); session.commit()
    reconcile_documents(session)
    session.refresh(bill)
    assert bill.settled_by_id == feed.id                   # the bill is paid by the feed line, the one that counts


def test_a_line_type_in_the_readers_own_words_no_longer_refuses_the_whole_statement():
    from app.categories.financials.handler import line_type
    assert line_type("atm_withdrawal") == TransactionType.DEBIT          # document 22
    assert line_type("insurance") == TransactionType.DEBIT               # document 33
    assert line_type("Credit") == TransactionType.CREDIT
    assert line_type("salary_received") == TransactionType.CREDIT
    assert line_type("bank_transfer") == TransactionType.TRANSFER


def test_a_long_statement_over_monthly_statements_shows_overlap_not_double_lines(session, monkeypatch, tmp_path):
    acct = _account(session)
    monthly = Document(filename="revolut-2026-10.pdf", file_path="/tmp/m.pdf", content_hash="h-m",
                       source=DocumentSource.MANUAL, paper_kind="statement", account_id=acct.id)
    session.add(monthly); session.commit()
    old = Transaction(document_id=monthly.id, description="UBER", amount=8.4, transaction_type=TransactionType.DEBIT,
                      paid_date=date(2026, 10, 14), account_id=acct.id)
    session.add(old); session.commit()
    before = _counted(session)
    doc = _read_statement(session, acct, monkeypatch, tmp_path, [_line(14, 8.4, "Uber"), _line(15, 3.0, "Coffee")])
    lines = {t.description: t for t in session.exec(select(Transaction).where(Transaction.document_id == doc.id))}
    assert lines["Uber"].settled_by_id == old.id and lines["Coffee"].settled_by_id is None
    assert _counted(session) == before + 1
