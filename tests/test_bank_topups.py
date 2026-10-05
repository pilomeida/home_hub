from datetime import date

from app.models.account import Account
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.transaction import Category, Transaction, TransactionType
from app.services.bankapi.sync import _provider
from app.services.bankapi.topups import link_revolut_topups


def _accounts(session):
    san = Account(name="Santander Current Account", institution="Santander Totta")
    rev = Account(name="Revolut Current Account", institution="Revolut Bank UAB")
    session.add(san); session.add(rev); session.commit()
    doc = Document(filename="d", file_path="", content_hash="h", source=DocumentSource.API,
                   status=DocumentStatus.PROCESSED, domain=Domain.FINANCIALS, category="statement")
    session.add(doc); session.commit()
    return san, rev, doc


def _santander_debit(session, san, doc, amount, paid):
    # Provider text produced by the real sync path from a raw bank row.
    provider = _provider({"creditor": {"name": "COMPRA ESTRANG 3315 Revolut  0008"}}, is_credit=False)
    t = Transaction(document_id=doc.id, provider=provider, amount=amount, paid_date=paid,
                    account_id=san.id, transaction_type=TransactionType.DEBIT)
    session.add(t); session.commit(); session.refresh(t)
    return t


def _revolut_credit(session, rev, doc, amount, paid):
    provider = _provider({"debtor": {"name": "Top-Up by *3871"}}, is_credit=True)
    t = Transaction(document_id=doc.id, provider=provider, amount=amount, paid_date=paid,
                    account_id=rev.id, transaction_type=TransactionType.CREDIT)
    session.add(t); session.commit(); session.refresh(t)
    return t


def test_pair_is_linked_and_typed_as_transfer(session):
    san, rev, doc = _accounts(session)
    credit = _revolut_credit(session, rev, doc, 540.0, date(2026, 9, 3))
    debit = _santander_debit(session, san, doc, 540.0, date(2026, 9, 7))

    assert link_revolut_topups(session) == 1

    session.refresh(credit); session.refresh(debit)
    assert debit.linked_transaction_id == credit.id and credit.linked_transaction_id == debit.id
    assert debit.transaction_type == credit.transaction_type == TransactionType.TRANSFER
    assert debit.category == credit.category == Category.TRANSFER


def test_rerun_is_a_noop(session):
    san, rev, doc = _accounts(session)
    _revolut_credit(session, rev, doc, 20.0, date(2026, 9, 22))
    _santander_debit(session, san, doc, 20.0, date(2026, 9, 23))
    assert link_revolut_topups(session) == 1
    assert link_revolut_topups(session) == 0


def test_different_amount_or_too_far_apart_or_wrong_order_stay_unlinked(session):
    san, rev, doc = _accounts(session)
    _revolut_credit(session, rev, doc, 20.0, date(2026, 9, 1))
    _santander_debit(session, san, doc, 20.01, date(2026, 9, 2))   # amount differs by a cent
    _revolut_credit(session, rev, doc, 30.0, date(2026, 9, 1))
    _santander_debit(session, san, doc, 30.0, date(2026, 9, 20))   # 19 days later
    _revolut_credit(session, rev, doc, 40.0, date(2026, 9, 10))
    _santander_debit(session, san, doc, 40.0, date(2026, 9, 9))    # debit before the credit
    assert link_revolut_topups(session) == 0


def test_extra_topup_is_left_alone_and_pairing_is_one_to_one(session):
    san, rev, doc = _accounts(session)
    _revolut_credit(session, rev, doc, 10.0, date(2026, 9, 9))
    _revolut_credit(session, rev, doc, 10.0, date(2026, 9, 11))
    _santander_debit(session, san, doc, 10.0, date(2026, 9, 14))
    assert link_revolut_topups(session) == 1
    left = session.query(Transaction).filter(Transaction.linked_transaction_id.is_(None)).all()
    assert len(left) == 1 and left[0].account_id == rev.id


def test_ordinary_santander_debits_are_ignored(session):
    san, rev, doc = _accounts(session)
    _revolut_credit(session, rev, doc, 25.0, date(2026, 9, 1))
    other = Transaction(document_id=doc.id, provider="Continente", amount=25.0,
                        paid_date=date(2026, 9, 2), account_id=san.id)
    session.add(other); session.commit()
    assert link_revolut_topups(session) == 0


def test_no_revolut_account_is_fine(session):
    assert link_revolut_topups(session) == 0
