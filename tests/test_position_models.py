from datetime import date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.debt import Debt, DebtKind
from app.models.document import Document, DocumentSource
from app.models.position import (
    BalanceSnapshot, DebtMatchRule, LoanAlert, LoanInsuranceRule, LoanMovement, LoanSnapshot, PositionExtraction, ReminderLog, SavingsSnapshot,
)


def _debt(session, **kw):
    d = Debt(kind=DebtKind.FORMAL, original_amount=1000.0, current_balance=900, **kw)
    session.add(d)
    session.commit()
    return d


def _doc(session, name="a.pdf"):
    d = Document(filename=name, file_path=name, content_hash=name, source=DocumentSource.MANUAL)
    session.add(d)
    session.commit()
    return d


def _twice(session, make):
    session.add(make())
    session.commit()
    session.add(make())
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_debt_old_way_defaults(session):
    d = _debt(session)
    session.refresh(d)
    assert d.status == "active"
    assert d.name is None and d.external_number is None and d.capital_granted is None
    assert d.term_months is None and d.start_date is None and d.loan_type is None


def test_debt_new_fields_round_trip(session):
    d = _debt(session, name="Mortgage", external_number="000100000000001", capital_granted=150000.0,
              term_months=360, start_date=date(2020, 1, 1), loan_type="mortgage")
    session.refresh(d)
    assert d.external_number == "000100000000001" and d.term_months == 360 and d.loan_type == "mortgage"


def test_loan_movement_unique_per_instalment(session):
    d, doc = _debt(session), _doc(session)
    mk = lambda: LoanMovement(debt_id=d.id, instalment_number=7, movement_date=date(2026, 1, 5),
                              capital=100.0, interest=20.0, document_id=doc.id)
    _twice(session, mk)
    m = session.query(LoanMovement).one()
    assert m.insurance == 0.0 and m.balance_after is None


def test_loan_snapshot_unique(session):
    d, doc = _debt(session), _doc(session)
    _twice(session, lambda: LoanSnapshot(debt_id=d.id, as_of=date(2026, 1, 1), capital_remaining=900.0,
                                         document_id=doc.id))


def test_savings_snapshot_unique(session):
    doc = _doc(session)
    _twice(session, lambda: SavingsSnapshot(as_of=date(2026, 1, 1), product="fund", label="F", account_ref="X1",
                                            value=10.0, document_id=doc.id))


def test_balance_snapshot_unique(session):
    doc = _doc(session)
    _twice(session, lambda: BalanceSnapshot(as_of=date(2026, 1, 1), kind="deposit", label="D", amount=5.0,
                                            document_id=doc.id))


def test_position_extraction_unique_document(session):
    doc = _doc(session)
    _twice(session, lambda: PositionExtraction(document_id=doc.id, status="ok"))
    pe = session.query(PositionExtraction).one()
    assert isinstance(pe.extracted_at, datetime) and pe.payload_json is None


def test_reminder_log_and_match_rule(session):
    d = _debt(session)
    session.add(ReminderLog(key="loan-1", sent_at=datetime(2026, 1, 1), cycle_start=date(2026, 1, 1)))
    session.add(DebtMatchRule(debt_id=d.id, normalized_key="joao"))
    session.commit()
    session.add(DebtMatchRule(debt_id=d.id, normalized_key="joao"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_loan_alert_defaults_and_unique_key(session):
    d = _debt(session)
    doc = _doc(session)
    make = lambda: LoanAlert(debt_id=d.id, kind="interest_only", ref="inst:1", message="m",
                             detected_on=date(2026, 1, 1), document_id=doc.id)
    _twice(session, make)
    a = session.query(LoanAlert).one()
    assert a.acknowledged is False and a.ack_note is None and a.acknowledged_at is None


def test_loan_alert_document_is_optional(session):
    d = _debt(session)
    session.add(LoanAlert(debt_id=d.id, kind="spread_changed", ref="snap:2026-01-01", message="m",
                          detected_on=date(2026, 1, 1)))
    session.commit()


def test_new_rate_fields_default_to_none(session):
    d = _debt(session)
    doc = _doc(session)
    s = LoanSnapshot(debt_id=d.id, as_of=date(2026, 1, 1), capital_remaining=1.0, document_id=doc.id)
    session.add(s)
    session.commit()
    session.refresh(s)
    session.refresh(d)
    assert s.indexante_percent is None and s.spread_percent is None and d.spread_percent is None


def test_position_rows_keep_living_without_their_document(session):
    """Block B: document_id is nullable on every position table."""
    d = _debt(session)
    session.add(LoanMovement(debt_id=d.id, instalment_number=1, movement_date=date(2026, 1, 5),
                             capital=1.0, interest=1.0))
    session.add(LoanSnapshot(debt_id=d.id, as_of=date(2026, 1, 1), capital_remaining=1.0))
    session.add(SavingsSnapshot(as_of=date(2026, 1, 1), label="F", account_ref="X", value=1.0))
    session.add(BalanceSnapshot(as_of=date(2026, 1, 1), kind="deposit", label="D", amount=1.0))
    session.commit()
    for model in (LoanMovement, LoanSnapshot, SavingsSnapshot, BalanceSnapshot):
        assert session.query(model).one().document_id is None


def test_loan_movement_insurance_parts_default_to_zero(session):
    d = _debt(session)
    session.add(LoanMovement(debt_id=d.id, instalment_number=1, movement_date=date(2026, 1, 5),
                             capital=1.0, interest=1.0))
    session.commit()
    m = session.query(LoanMovement).one()
    assert m.insurance_life == 0.0 and m.insurance_building == 0.0


def test_loan_insurance_rule_key_is_unique(session):
    d = _debt(session)
    session.add(LoanInsuranceRule(debt_id=d.id, component="life", normalized_key="seg vida 1"))
    session.commit()
    session.add(LoanInsuranceRule(debt_id=d.id, component="building", normalized_key="seg vida 1"))
    with pytest.raises(IntegrityError):
        session.commit()
