"""Informal person-to-person loans: the explicit ledger (debt_entries), its
balance, role derivation, filing of linked transactions and the legacy backfill.
All data is synthetic."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.models.debt import Debt, DebtDirection, DebtKind
from app.models.document import Document, DocumentSource
from app.models.person import Person
from app.models.position import DebtEntry
from app.models.transaction import Transaction, TransactionType
from app.services import debt_ledger as ledger
from app.services.loan_math import InformalSummary, informal_balance, informal_summary
from app.services.taxonomy import ensure_taxonomy, get_node

LENT = DebtDirection.OWED_TO_US
BORROWED = DebtDirection.OWED_BY_US
_n = [0]


def _doc(session):
    _n[0] += 1
    d = Document(filename=f"l{_n[0]}.pdf", file_path=f"/tmp/l{_n[0]}.pdf", content_hash=f"ledger-{_n[0]}",
                 source=DocumentSource.MANUAL)
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def _txn(session, amount, ttype=TransactionType.DEBIT, paid=date(2026, 1, 10), provider="TRF SEPA+ P/ TEST PERSON"):
    t = Transaction(document_id=_doc(session).id, provider=provider, amount=amount, transaction_type=ttype,
                    paid_date=paid)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _debt(session, direction=LENT):
    d = ledger.create_informal_debt(session, "Test Person", direction)
    session.commit()
    session.refresh(d)
    return d


def _slug_of(session, t):
    from app.models.category_node import CategoryNode
    session.refresh(t)
    return session.get(CategoryNode, t.category_id).slug if t.category_id else None


# --- model ---------------------------------------------------------------

def test_debt_entry_transaction_id_is_unique_but_nullable_repeats(session):
    d = _debt(session)
    t = _txn(session, 10.0)
    session.add(DebtEntry(debt_id=d.id, kind="advance", amount=1.0, entry_date=date(2026, 1, 1)))
    session.add(DebtEntry(debt_id=d.id, kind="advance", amount=2.0, entry_date=date(2026, 1, 2)))
    session.commit()  # several NULL transaction_ids are fine
    session.add(DebtEntry(debt_id=d.id, kind="advance", amount=1.0, entry_date=date(2026, 1, 1), transaction_id=t.id))
    session.commit()
    session.add(DebtEntry(debt_id=d.id, kind="repayment", amount=1.0, entry_date=date(2026, 1, 1), transaction_id=t.id))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --- balance -------------------------------------------------------------

def test_lend_1000_repay_300_add_on_500_is_1200(session):
    d = _debt(session)
    ledger.link_transaction_as_entry(session, d, _txn(session, 1000.0, paid=date(2026, 1, 1)), None)
    ledger.link_transaction_as_entry(session, d, _txn(session, 300.0, TransactionType.CREDIT, date(2026, 2, 1)), None)
    ledger.link_transaction_as_entry(session, d, _txn(session, 500.0, TransactionType.DEBIT, date(2026, 3, 1)), None)
    session.commit()
    session.refresh(d)
    assert d.current_balance == Decimal("1200.00")
    assert d.original_amount == 1500.0  # Σ advances
    s = informal_summary(session, d)
    assert isinstance(s, InformalSummary)
    assert (s.advances_total, s.repayments_total, s.adjustments_net) == (Decimal("1500.00"), Decimal("300.00"), Decimal("0.00"))
    assert s.balance == Decimal("1200.00") and s.overpaid_by == Decimal("0.00")
    assert s.last_activity == date(2026, 3, 1)
    assert [r for _e, r in s.entries] == [Decimal("1000.00"), Decimal("700.00"), Decimal("1200.00")]
    assert informal_balance(session, d) == Decimal("1200.00")


def test_borrowed_direction_is_mirrored(session):
    d = _debt(session, BORROWED)
    ledger.link_transaction_as_entry(session, d, _txn(session, 1000.0, TransactionType.CREDIT, date(2026, 1, 1)), None)
    ledger.link_transaction_as_entry(session, d, _txn(session, 300.0, TransactionType.DEBIT, date(2026, 2, 1)), None)
    ledger.link_transaction_as_entry(session, d, _txn(session, 500.0, TransactionType.CREDIT, date(2026, 3, 1)), None)
    session.commit()
    session.refresh(d)
    assert d.current_balance == Decimal("1200.00")
    kinds = [e.kind for e in session.exec(select(DebtEntry).order_by(DebtEntry.id)).all()]
    assert kinds == ["advance", "repayment", "advance"]


def test_default_kind_table():
    k = ledger.default_kind
    assert k(LENT, TransactionType.DEBIT) == "advance" and k(LENT, TransactionType.CREDIT) == "repayment"
    assert k(BORROWED, TransactionType.CREDIT) == "advance" and k(BORROWED, TransactionType.DEBIT) == "repayment"
    assert k(LENT, TransactionType.TRANSFER) is None and k(BORROWED, TransactionType.TRANSFER) is None
    assert k(None, TransactionType.DEBIT) == "repayment"  # legacy: no direction counts as owed by us
    assert ledger.transfer_default_kind(LENT) == "advance" and ledger.transfer_default_kind(BORROWED) == "repayment"


def test_transfer_type_needs_an_explicit_role(session):
    d = _debt(session)
    t = _txn(session, 100.0, TransactionType.TRANSFER)
    with pytest.raises(ValueError):
        ledger.link_transaction_as_entry(session, d, t, None)
    session.refresh(t)
    assert t.debt_id is None
    e = ledger.link_transaction_as_entry(session, d, t, "repayment")
    session.commit()
    assert e.kind == "repayment" and t.debt_id == d.id


def test_role_override_at_link_time(session):
    d = _debt(session)  # lent: a DEBIT is normally an advance
    t = _txn(session, 80.0, TransactionType.DEBIT)
    e = ledger.link_transaction_as_entry(session, d, t, "repayment")  # e.g. a refund sent back
    assert e.kind == "repayment"
    with pytest.raises(ValueError):
        ledger.link_transaction_as_entry(session, d, _txn(session, 5.0), "adjust_up")  # only advance|repayment


def test_manual_cash_advance_and_adjustment(session):
    d = _debt(session)
    ledger.add_entry(session, d, "advance", 200.0, date(2025, 12, 1), note="cash")
    ledger.add_entry(session, d, "adjust_down", 25.5, date(2025, 12, 5), note="gave back change")
    ledger.add_entry(session, d, "adjust_up", 10, date(2025, 12, 6))
    session.commit()
    session.refresh(d)
    assert d.current_balance == Decimal("184.50")
    assert informal_summary(session, d).adjustments_net == Decimal("-15.50")


@pytest.mark.parametrize("amount", [0, -5, float("nan"), float("inf")])
def test_add_entry_rejects_bad_amounts(session, amount):
    d = _debt(session)
    with pytest.raises(ValueError):
        ledger.add_entry(session, d, "advance", amount, date(2026, 1, 1))


def test_add_entry_rejects_unknown_kind_and_statement_loans(session):
    d = _debt(session)
    with pytest.raises(ValueError):
        ledger.add_entry(session, d, "gift", 5.0, date(2026, 1, 1))
    loan = Debt(kind=DebtKind.FORMAL, original_amount=1.0, current_balance=Decimal("1"), external_number="1234567")
    session.add(loan)
    session.commit()
    with pytest.raises(ValueError):
        ledger.add_entry(session, loan, "advance", 5.0, date(2026, 1, 1))


def test_delete_manual_entry_and_refuse_a_linked_one(session):
    d = _debt(session)
    manual = ledger.add_entry(session, d, "advance", 100.0, date(2026, 1, 1))
    linked = ledger.link_transaction_as_entry(session, d, _txn(session, 50.0), None)
    session.commit()
    ledger.delete_manual_entry(session, manual.id)
    session.commit()
    session.refresh(d)
    assert d.current_balance == Decimal("50.00") and d.original_amount == 50.0
    with pytest.raises(ValueError):
        ledger.delete_manual_entry(session, linked.id)
    with pytest.raises(LookupError):
        ledger.delete_manual_entry(session, 9999)
    assert session.get(DebtEntry, linked.id) is not None


def test_overpayment_shows_overpaid_and_stores_zero(session):
    d = _debt(session)
    ledger.add_entry(session, d, "advance", 100.0, date(2026, 1, 1))
    ledger.add_entry(session, d, "repayment", 130.0, date(2026, 2, 1))
    session.commit()
    session.refresh(d)
    s = informal_summary(session, d)
    assert d.current_balance == Decimal("0.00")
    assert s.balance == Decimal("0.00") and s.overpaid_by == Decimal("30.00")
    assert s.entries[-1][1] == Decimal("-30.00")


def test_create_informal_debt_with_opening_entry(session):
    d = ledger.create_informal_debt(session, "  Test Person ", BORROWED, opening_amount=250.0,
                                    entry_date=date(2025, 6, 1), note="cash from a friend")
    session.commit()
    assert d.kind == DebtKind.INFORMAL and d.direction == BORROWED
    assert d.current_balance == Decimal("250.00") and d.original_amount == 250.0
    e = session.exec(select(DebtEntry)).one()
    assert (e.kind, e.transaction_id, e.note) == ("advance", None, "cash from a friend")
    assert session.get(Person, d.person_id).name == "Test Person"
    again = ledger.create_informal_debt(session, "Test Person", LENT)  # same person re-used
    assert again.person_id == d.person_id
    assert len(session.exec(select(Person)).all()) == 1


# --- linking guards ------------------------------------------------------

def test_never_overwrites_a_different_debt_id(session):
    a, b = _debt(session), _debt(session)
    t = _txn(session, 10.0)
    ledger.link_transaction_as_entry(session, a, t, None)
    with pytest.raises(ValueError):
        ledger.link_transaction_as_entry(session, b, t, None)
    session.refresh(t)
    assert t.debt_id == a.id
    # linking again to the same debt is idempotent
    ledger.link_transaction_as_entry(session, a, t, None)
    assert len(session.exec(select(DebtEntry)).all()) == 1


def test_linking_marks_the_candidate_reviewed(session):
    d = _debt(session)
    t = _txn(session, 10.0)
    ledger.link_transaction_as_entry(session, d, t, None)
    assert t.debt_candidate_reviewed is True


# --- filing --------------------------------------------------------------

@pytest.mark.parametrize("direction,ttype,role,slug", [
    (LENT, TransactionType.DEBIT, None, "loans-debt.money-lent-out.loan-to-friends"),
    (LENT, TransactionType.CREDIT, None, "loans-debt-in.repayments-received.from-friends"),
    (BORROWED, TransactionType.CREDIT, None, "loans-debt-in.money-borrowed.personal-loan-received"),
    (BORROWED, TransactionType.DEBIT, None, "loans-debt.loan-repayments.personal-loans"),
])
def test_linked_transaction_is_filed_under_the_matching_node(session, direction, ttype, role, slug):
    ensure_taxonomy(session)
    session.commit()
    d = _debt(session, direction)
    t = _txn(session, 40.0, ttype)
    ledger.link_transaction_as_entry(session, d, t, role)
    session.commit()
    assert _slug_of(session, t) == slug


def test_direction_mismatch_leaves_the_filing_as_is(session):
    ensure_taxonomy(session)
    other = get_node(session, "loans-debt.loan-repayments.personal-loans")
    d = _debt(session, LENT)
    t = _txn(session, 40.0, TransactionType.DEBIT)
    t.category_id = other.id
    session.add(t)
    session.commit()
    # role override: a DEBIT as a repayment on a lent debt -> node is an "in" node: no match
    ledger.link_transaction_as_entry(session, d, t, "repayment")
    session.commit()
    assert _slug_of(session, t) == "loans-debt.loan-repayments.personal-loans"
    # TRANSFER-type never matches an in/out node either
    tr = _txn(session, 40.0, TransactionType.TRANSFER)
    ledger.link_transaction_as_entry(session, d, tr, "advance")
    session.commit()
    assert _slug_of(session, tr) is None


# --- rebuild from legacy links -------------------------------------------

def _legacy(session, direction, original):
    d = Debt(kind=DebtKind.INFORMAL, direction=direction, original_amount=original,
             current_balance=Decimal(str(original)))
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def _legacy_link(session, d, t):
    t.debt_id = d.id
    session.add(t)
    session.commit()


def test_rebuild_creating_transaction_is_the_first_advance_and_is_idempotent(session):
    d = _legacy(session, LENT, 3000.0)
    _legacy_link(session, d, _txn(session, 3000.0, TransactionType.DEBIT, date(2026, 1, 1)))
    _legacy_link(session, d, _txn(session, 500.0, TransactionType.CREDIT, date(2026, 2, 1)))
    _legacy_link(session, d, _txn(session, 120.0, TransactionType.TRANSFER, date(2026, 3, 1)))  # debt default: advance
    created = ledger.rebuild_entries_from_links(session)
    session.commit()
    assert created == 3
    session.refresh(d)
    kinds = [e.kind for e in session.exec(select(DebtEntry).order_by(DebtEntry.entry_date)).all()]
    assert kinds == ["advance", "repayment", "advance"]
    assert d.current_balance == Decimal("2620.00")
    assert ledger.rebuild_entries_from_links(session) == 0  # idempotent
    session.commit()
    assert len(session.exec(select(DebtEntry)).all()) == 3
    session.refresh(d)
    assert d.current_balance == Decimal("2620.00")


def test_rebuild_keeps_an_opening_balance_when_original_is_not_a_transaction(session):
    d = _legacy(session, BORROWED, 1000.0)
    _legacy_link(session, d, _txn(session, 250.0, TransactionType.DEBIT))  # repayment
    _legacy_link(session, d, _txn(session, 100.0, TransactionType.TRANSFER))  # owed by us: repayment
    ledger.rebuild_entries_from_links(session)
    session.commit()
    session.refresh(d)
    entries = session.exec(select(DebtEntry).order_by(DebtEntry.id)).all()
    assert entries[0].transaction_id is None and entries[0].kind == "advance" and entries[0].amount == 1000.0
    assert d.current_balance == Decimal("650.00")


def test_rebuild_skips_statement_loans_and_does_not_refile(session):
    ensure_taxonomy(session)
    loan = Debt(kind=DebtKind.FORMAL, original_amount=5.0, current_balance=Decimal("5"), external_number="7654321")
    session.add(loan)
    session.commit()
    t = _txn(session, 5.0)
    _legacy_link(session, loan, t)
    d = _legacy(session, LENT, 40.0)
    t2 = _txn(session, 40.0)
    _legacy_link(session, d, t2)
    ledger.rebuild_entries_from_links(session)
    session.commit()
    assert session.exec(select(DebtEntry).where(DebtEntry.debt_id == loan.id)).first() is None
    assert _slug_of(session, t2) is None  # a rebuild never re-files history


# --- fix wave C1: a ledger entry survives its transaction's withdrawal -----

def _fk_stmt_doc(session, name="stmt.pdf"):
    d = Document(filename=name, file_path=f"/tmp/{name}", content_hash=f"h-{name}", source=DocumentSource.MANUAL)
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def _fk_txn(session, doc, amount, ttype=TransactionType.DEBIT, paid=date(2026, 1, 10)):
    t = Transaction(document_id=doc.id, provider="TRF SEPA+ P/ TEST PERSON", amount=amount, transaction_type=ttype,
                    paid_date=paid)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


@pytest.mark.asyncio
async def test_withdraw_keeps_the_ledger_entry_detached_and_the_balance(fk_session):
    from app.domains.financials.handler import FinancialsHandler
    s = fk_session
    doc = _fk_stmt_doc(s)
    debt = ledger.create_informal_debt(s, "Test Person", LENT)
    ledger.link_transaction_as_entry(s, debt, _fk_txn(s, doc, 1000.0), None)
    ledger.add_entry(s, debt, "advance", 50.0, date(2026, 1, 1), note="cash")
    s.commit()
    await FinancialsHandler().withdraw(s, doc)  # used to raise IntegrityError with foreign keys on
    s.expire_all()
    entries = s.exec(select(DebtEntry).order_by(DebtEntry.id)).all()
    assert len(entries) == 2 and all(e.transaction_id is None for e in entries)
    assert (entries[0].kind, entries[0].amount, entries[0].entry_date) == ("advance", 1000.0, date(2026, 1, 10))
    assert ledger.WITHDRAWN_NOTE in entries[0].note
    assert entries[1].note == "cash"  # untouched manual entry
    assert s.get(Debt, debt.id).current_balance == Decimal("1050.00")
    assert s.exec(select(Transaction)).all() == []


@pytest.mark.asyncio
async def test_withdraw_appends_to_an_existing_note(fk_session):
    from app.domains.financials.handler import FinancialsHandler
    s = fk_session
    doc = _fk_stmt_doc(s)
    debt = ledger.create_informal_debt(s, "Test Person", LENT)
    e = ledger.link_transaction_as_entry(s, debt, _fk_txn(s, doc, 10.0), None)
    e.note = "mine"
    s.add(e)
    s.commit()
    await FinancialsHandler().withdraw(s, doc)
    s.expire_all()
    note = s.get(DebtEntry, e.id).note
    assert note.startswith("mine") and ledger.WITHDRAWN_NOTE in note


def test_relink_reattaches_the_detached_entry_instead_of_adding_one(fk_session):
    s = fk_session
    doc = _fk_stmt_doc(s)
    debt = ledger.create_informal_debt(s, "Test Person", LENT)
    t = _fk_txn(s, doc, 1000.0)
    first = ledger.link_transaction_as_entry(s, debt, t, None)
    s.commit()
    ledger.detach_entries_for_transactions(s, [t.id])
    s.delete(t)
    s.commit()
    again = _fk_txn(s, _fk_stmt_doc(s, "again.pdf"), 1000.0)  # re-upload: a new transaction, same data
    entry = ledger.link_transaction_as_entry(s, debt, again, None)
    s.commit()
    assert entry.id == first.id and entry.transaction_id == again.id
    assert ledger.WITHDRAWN_NOTE not in (entry.note or "")
    assert len(s.exec(select(DebtEntry)).all()) == 1
    s.refresh(debt)
    assert debt.current_balance == Decimal("1000.00")


def test_relink_does_not_reattach_a_different_amount_or_a_manual_entry(fk_session):
    s = fk_session
    debt = ledger.create_informal_debt(s, "Test Person", LENT)
    ledger.add_entry(s, debt, "advance", 1000.0, date(2026, 1, 10), note="typed by hand")  # not detached
    t = _fk_txn(s, _fk_stmt_doc(s), 1000.0)
    ledger.link_transaction_as_entry(s, debt, t, None)
    s.commit()
    assert len(s.exec(select(DebtEntry)).all()) == 2


@pytest.mark.asyncio
async def test_match_rule_auto_link_reattaches_after_a_reupload(fk_session):
    from app.domains.financials.handler import FinancialsHandler
    from app.models.merchant import Merchant
    from app.models.position import DebtMatchRule
    from app.models.transaction import Category, Nature
    from app.services.classification_engine import classify_transaction, normalize_provider

    s = fk_session
    ensure_taxonomy(s)
    debt = ledger.create_informal_debt(s, "Test Person", LENT)
    s.add(DebtMatchRule(debt_id=debt.id, normalized_key=normalize_provider("TRF SEPA+ P/ TEST PERSON")))
    s.add(Merchant(canonical_name="Test Person", default_category=Category.OTHER_EXPENSE, default_nature=Nature.ESSENTIAL,
                   normalized_key=normalize_provider("TRF SEPA+ P/ TEST PERSON")))
    doc = _fk_stmt_doc(s)
    t = _fk_txn(s, doc, 400.0)
    s.commit()
    await classify_transaction(s, t, gateway=None)
    s.commit()
    await FinancialsHandler().withdraw(s, doc)
    doc2 = _fk_stmt_doc(s, "again.pdf")
    t2 = _fk_txn(s, doc2, 400.0)
    await classify_transaction(s, t2, gateway=None)
    s.commit()
    assert len(s.exec(select(DebtEntry)).all()) == 1
    s.refresh(debt)
    assert debt.current_balance == Decimal("400.00")
    assert s.exec(select(DebtEntry)).one().transaction_id == t2.id


# --- fix wave I3: identify the creating transaction by amount ---------------

def _legacy_with_ids(session, direction, original, specs):
    d = _legacy(session, direction, original)
    for tid, amount, ttype, day in specs:
        t = Transaction(id=tid, document_id=_doc(session).id, provider="TRF P/ TEST PERSON", amount=amount,
                        transaction_type=ttype, paid_date=date(2026, 1, day), debt_id=d.id)
        session.add(t)
    session.commit()
    return d


def test_rebuild_finds_the_creating_transaction_by_amount_not_by_id(session):
    # the debt was created from txn 500 (1000); an older txn 300 (200) was linked afterwards
    d = _legacy_with_ids(session, LENT, 1000.0, [(300, 200.0, TransactionType.DEBIT, 5), (500, 1000.0, TransactionType.DEBIT, 20)])
    ledger.rebuild_entries_from_links(session)
    session.commit()
    session.refresh(d)
    assert d.current_balance == Decimal("1200.00") and d.original_amount == 1200.0
    assert len(session.exec(select(DebtEntry)).all()) == 2
    assert ledger.rebuild_entries_from_links(session) == 0
    session.commit()
    session.refresh(d)
    assert d.current_balance == Decimal("1200.00")


def test_rebuild_prefers_the_lowest_id_among_equal_amounts(session):
    d = _legacy_with_ids(session, LENT, 1000.0, [(10, 1000.0, TransactionType.DEBIT, 1), (11, 1000.0, TransactionType.CREDIT, 2)])
    ledger.rebuild_entries_from_links(session)
    session.commit()
    session.refresh(d)
    assert d.current_balance == Decimal("0.00")  # advance 1000 (creating) then a 1000 repayment


def test_rebuild_without_an_equal_amount_adds_one_opening_entry_and_treats_all_as_ordinary(session):
    d = _legacy_with_ids(session, LENT, 1000.0, [(3, 200.0, TransactionType.DEBIT, 5), (4, 50.0, TransactionType.CREDIT, 6)])
    ledger.rebuild_entries_from_links(session)
    session.commit()
    session.refresh(d)
    opening = [e for e in session.exec(select(DebtEntry)).all() if e.transaction_id is None]
    assert len(opening) == 1 and opening[0].amount == 1000.0 and opening[0].note == "opening balance (migrated)"
    assert d.current_balance == Decimal("1150.00")
    assert ledger.rebuild_entries_from_links(session) == 0
