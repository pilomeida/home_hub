"""Loan insurance debits (SEG VIDA / SEG:EDF / SEG:LAR) tied to their loans.
All loans, numbers and amounts are synthetic."""

from datetime import date

import pytest
from sqlalchemy import event
from sqlmodel import select

from app.models.debt import Debt, DebtDirection, DebtKind
from app.models.document import Document, DocumentSource
from app.models.position import LoanInsuranceRule, LoanMovement
from app.models.transaction import Transaction, TransactionType
from app.services.loan_insurance import insurance_key, link_insurance_transaction
from app.services.loan_linking import link_all_unlinked
from app.services.taxonomy import ensure_taxonomy, file_transaction, get_node, legacy_category_for

LIFE_SLUG = "insurances.home.life-insurance-house-loan"
BUILDING_SLUG = "insurances.home.building-insurance-house-loan"


@pytest.fixture()
def tree(session):
    ensure_taxonomy(session)


def _doc(session):
    d = session.exec(select(Document)).first()
    if d:
        return d
    d = Document(filename="s.pdf", file_path="/tmp/s.pdf", content_hash="h-ins", source=DocumentSource.MANUAL)
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def _loan(session, number, life=0.0, building=0.0, when=date(2026, 9, 2), name=None):
    debt = Debt(kind=DebtKind.FORMAL, direction=DebtDirection.OWED_BY_US, original_amount=1000.0,
                current_balance=900, name=name or f"Loan {number[-4:]}", external_number=number,
                loan_type="mortgage")
    session.add(debt)
    session.commit()
    session.refresh(debt)
    session.add(LoanMovement(
        debt_id=debt.id, instalment_number=1, movement_date=when, capital=100.0, interest=20.0,
        insurance=life + building, insurance_life=life, insurance_building=building,
        document_id=_doc(session).id))
    session.commit()
    return debt


def _txn(session, provider, amount, paid=date(2026, 9, 3), ttype=TransactionType.DEBIT):
    t = Transaction(document_id=_doc(session).id, provider=provider, amount=amount,
                    transaction_type=ttype, paid_date=paid)
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


# --- insurance_key ----------------------------------------------------------

@pytest.mark.parametrize("provider, key", [
    ("SEG:EDF 2026-09-02/2026-10-01", "seg edf"),
    ("SEG:LAR 2026-09-02/2026-10-01", "seg lar"),
    ("SEG VIDA 15.123456-2026/08/21", "seg vida 15.123456"),
    ("seg vida 15.123456", "seg vida 15.123456"),
    ("SEG VIDA 15.123456 2026/08/21", "seg vida 15.123456"),
    ("SEG:EDF   2026-09-02", "seg edf"),
    ("SEG EDF 2026-09-02/2026-10-01", "seg edf"),
    ("SEGURANCA SOCIAL", None),
    ("SEGURO CASA", None),
    ("COB.REC.31.000100000000001/ 35", None),
    ("", None),
    (None, None),
])
def test_insurance_key(provider, key):
    assert insurance_key(provider) == key


# --- amount match and rule creation ----------------------------------------

def test_amount_match_links_files_and_creates_the_rule(session, tree):
    debt = _loan(session, "000100000000001", life=8.0, building=20.0)
    t = _txn(session, "SEG:EDF 2026-09-02/2026-10-01", 20.00)
    assert link_insurance_transaction(session, t) is True
    session.commit()
    session.refresh(t)
    node = get_node(session, BUILDING_SLUG)
    assert t.debt_id == debt.id and t.category_id == node.id and t.debt_candidate_reviewed is True
    assert t.category == legacy_category_for(session, node)  # node + legacy category agree
    rule = session.exec(select(LoanInsuranceRule)).one()
    assert (rule.debt_id, rule.component, rule.normalized_key) == (debt.id, "building", "seg edf")


def test_life_component_files_under_the_life_node(session, tree):
    debt = _loan(session, "000100000000001", life=8.0, building=20.0)
    t = _txn(session, "SEG VIDA 15.000001-2026/08/21", 8.0)
    assert link_insurance_transaction(session, t) is True
    assert t.debt_id == debt.id and t.category_id == get_node(session, LIFE_SLUG).id
    assert session.exec(select(LoanInsuranceRule)).one().normalized_key == "seg vida 15.000001"


def test_changed_premium_links_through_the_rule(session, tree):
    debt = _loan(session, "000100000000001", building=20.0)
    assert link_insurance_transaction(session, _txn(session, "SEG:EDF 2026-09-02/2026-10-01", 20.0)) is True
    later = _txn(session, "SEG:EDF 2026-10-02/2026-11-01", 21.50, paid=date(2026, 10, 3))
    assert link_insurance_transaction(session, later) is True
    assert later.debt_id == debt.id and later.category_id == get_node(session, BUILDING_SLUG).id
    assert len(session.exec(select(LoanInsuranceRule)).all()) == 1


def test_tolerance_and_window_edges_are_inclusive(session, tree):
    _loan(session, "000100000000001", building=20.0, when=date(2026, 9, 2))
    assert link_insurance_transaction(session, _txn(session, "SEG:LAR 1", 20.01, paid=date(2026, 9, 9))) is True


def test_outside_tolerance_or_window_is_not_linked(session, tree):
    _loan(session, "000100000000001", building=20.0, when=date(2026, 9, 2))
    assert link_insurance_transaction(session, _txn(session, "SEG:EDF 1", 20.02)) is False
    assert link_insurance_transaction(session, _txn(session, "SEG:EDF 2", 20.0, paid=date(2026, 9, 10))) is False
    assert link_insurance_transaction(session, _txn(session, "SEG:EDF 3", 20.0, paid=date(2026, 8, 25))) is False
    assert link_insurance_transaction(session, _txn(session, "SEG:EDF 4", 20.0, paid=None)) is False
    assert session.exec(select(LoanInsuranceRule)).all() == []


def test_two_loans_with_the_same_insurance_amount_are_ambiguous(session, tree):
    _loan(session, "000100000000001", building=20.0)
    _loan(session, "000100000000002", building=20.0)
    t = _txn(session, "SEG:EDF 2026-09-02/2026-10-01", 20.0)
    assert link_insurance_transaction(session, t) is False
    assert t.debt_id is None and session.exec(select(LoanInsuranceRule)).all() == []


def test_equal_life_and_building_amounts_are_resolved_by_the_key_family(session, tree):
    # SEG:EDF can only be the building component, so equal amounts are no longer ambiguous
    debt = _loan(session, "000100000000001", life=10.0, building=10.0)
    t = _txn(session, "SEG:EDF 2026-09-02/2026-10-01", 10.0)
    assert link_insurance_transaction(session, t) is True and t.debt_id == debt.id
    assert session.exec(select(LoanInsuranceRule)).one().component == "building"


def test_non_seg_providers_and_credits_are_untouched(session, tree):
    _loan(session, "000100000000001", building=20.0)
    assert link_insurance_transaction(session, _txn(session, "SEGURANCA SOCIAL", 20.0)) is False
    assert link_insurance_transaction(session, _txn(session, "SOME SHOP", 20.0)) is False
    credit = _txn(session, "SEG:EDF 2026-09-02/2026-10-01", 20.0, ttype=TransactionType.CREDIT)
    assert link_insurance_transaction(session, credit) is False and credit.debt_id is None
    assert session.exec(select(LoanInsuranceRule)).all() == []


def test_never_overwrites_an_existing_debt_and_is_idempotent(session, tree):
    debt = _loan(session, "000100000000001", building=20.0)
    other = _loan(session, "000100000000002", building=5.0)
    t = _txn(session, "SEG:EDF 2026-09-02/2026-10-01", 20.0)
    t.debt_id = other.id
    session.add(t)
    session.commit()
    assert link_insurance_transaction(session, t) is False and t.debt_id == other.id
    free = _txn(session, "SEG:EDF 2026-09-02/2026-10-01", 20.0)
    assert link_insurance_transaction(session, free) is True and free.debt_id == debt.id
    assert link_insurance_transaction(session, free) is False  # already linked
    assert len(session.exec(select(LoanInsuranceRule)).all()) == 1


def test_existing_rule_wins_over_amount(session, tree):
    a = _loan(session, "000100000000001", building=20.0)
    b = _loan(session, "000100000000002", life=7.0)
    session.add(LoanInsuranceRule(debt_id=b.id, component="building", normalized_key="seg edf"))
    session.commit()
    t = _txn(session, "SEG:EDF 2026-09-02/2026-10-01", 20.0)
    assert link_insurance_transaction(session, t) is True
    assert t.debt_id == b.id and t.category_id == get_node(session, BUILDING_SLUG).id and a.id != b.id


# --- history back-link ------------------------------------------------------

def test_link_all_unlinked_links_insurance_debits_after_the_loan_appears(session, tree):
    ts = [_txn(session, "SEG:EDF 2026-09-02/2026-10-01", 20.0), _txn(session, "SOME SHOP", 20.0)]
    assert link_all_unlinked(session) == 0  # no loans yet
    debt = _loan(session, "000100000000001", building=20.0)
    assert link_all_unlinked(session) == 1
    session.expire_all()
    assert ts[0].debt_id == debt.id and ts[1].debt_id is None
    assert link_all_unlinked(session) == 0


def test_link_all_unlinked_uses_a_rule_for_every_month_and_loads_once(session, tree):
    debt = _loan(session, "000100000000001", building=20.0)
    ts = [_txn(session, f"SEG:EDF 2026-{m:02d}-02/2026-{m + 1:02d}-01", 20.0 + m / 10, paid=date(2026, m, 3))
          for m in range(1, 7)]
    # the premium changes every month: only the stored rule can link them
    session.add(LoanInsuranceRule(debt_id=debt.id, component="building", normalized_key="seg edf"))
    session.commit()
    seen = []
    engine = session.get_bind()

    def count(conn, cursor, statement, *a):
        if "FROM debts" in statement or "FROM loan_movements" in statement or "FROM loan_insurance_rules" in statement:
            seen.append(statement)
    event.listen(engine, "before_cursor_execute", count)
    try:
        assert link_all_unlinked(session) == 6
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(seen) <= 3  # loans, movements, rules: once each, not per row


def test_instalment_link_still_wins_over_insurance(session, tree):
    debt = _loan(session, "000100000000001", building=20.0)
    t = _txn(session, "COB.REC.31.000100000000001/ 35", 20.0)
    assert link_all_unlinked(session) == 1
    assert t.debt_id == debt.id and t.category_id == get_node(session, "loans-debt.loan-repayments.mortgage").id


def test_insurance_failure_is_non_fatal_in_link_all(session, tree, monkeypatch):
    from app.services import loan_linking
    _loan(session, "000100000000001", building=20.0)
    t = _txn(session, "COB.REC.31.000100000000001/ 35", 20.0)
    seg = _txn(session, "SEG:EDF 2026-09-02/2026-10-01", 20.0)

    def boom(*a, **k):
        raise RuntimeError("insurance exploded")
    monkeypatch.setattr(loan_linking, "link_insurance_transaction", boom)
    assert link_all_unlinked(session) == 1  # the instalment link is unaffected
    session.expire_all()
    assert t.debt_id is not None and seg.debt_id is None


# --- budget: ordinary spend, not an instalment ------------------------------

def test_linked_life_insurance_debit_is_spend_and_not_a_loan_instalment(session, tree):
    from app.services.budget_service import get_budget_overview
    today = date(2026, 10, 15)
    debt = _loan(session, "000100000000001", life=8.0)
    t = _txn(session, "SEG VIDA 15.000001-2026/08/21", 8.0, paid=date(2026, 10, 3))
    session.add(LoanMovement(debt_id=debt.id, instalment_number=2, movement_date=date(2026, 10, 2),
                             capital=1.0, interest=1.0, insurance=8.0, insurance_life=8.0,
                             document_id=_doc(session).id))
    session.commit()
    assert link_insurance_transaction(session, t) is True
    session.commit()
    ov = get_budget_overview(session, today, "month")
    assert ov.spend_spent == pytest.approx(8.0)
    assert ov.loan_instalments_paid == 0.0


def test_node_slugs_and_cadence(session, tree):
    for slug in (LIFE_SLUG, BUILDING_SLUG):
        node = get_node(session, slug)
        assert node.kind == "out" and node.cadence == "monthly" and node.level == 3


# --- fix wave: key families and component fit -------------------------------

def test_colon_and_space_give_the_same_key():
    assert insurance_key("SEG:EDF 2026-09-02/2026-10-01") == insurance_key("SEG EDF 2026-09-02/2026-10-01") == "seg edf"


@pytest.mark.parametrize("provider", ["SEG:XYZ 2026-09-02/2026-10-01", "SEG AUTO 2026-09-02", "SEG SOCIAL 15.1"])
def test_other_seg_debits_never_link_or_create_rules(session, tree, provider):
    _loan(session, "000100000000001", life=20.0, building=20.0)
    t = _txn(session, provider, 20.0)
    assert link_insurance_transaction(session, t) is False
    assert t.debt_id is None and session.exec(select(LoanInsuranceRule)).all() == []


def test_rule_with_a_component_that_does_not_fit_its_key_is_not_used(session, tree):
    debt = _loan(session, "000100000000001", life=7.0)
    session.add(LoanInsuranceRule(debt_id=debt.id, component="life", normalized_key="seg edf"))
    session.commit()
    t = _txn(session, "SEG:EDF 2026-09-02/2026-10-01", 7.0)
    assert link_insurance_transaction(session, t) is False and t.debt_id is None


def test_vida_matches_only_the_life_component(session, tree):
    _loan(session, "000100000000001", building=20.0)  # no life premium on any loan
    t = _txn(session, "SEG VIDA 15.000001-2026/08/21", 20.0)
    assert link_insurance_transaction(session, t) is False
    assert session.exec(select(LoanInsuranceRule)).all() == []
