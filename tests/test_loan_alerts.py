"""Loan red flags: interest-only instalments, spread changes, inconsistent rate.
All data is synthetic."""
import logging
from datetime import date

import pytest
from sqlmodel import select

from app.models.debt import Debt
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.position import LoanAlert, LoanMovement, LoanSnapshot
from app.services import position_store
from app.services.loan_alerts import acknowledge_alert
from app.services.overview_service import get_needs_attention
from app.services.position_extraction import ExtractedLoan, ExtractedPositions, Instalment
from app.services.position_store import apply_loan_history, apply_positions, create_loan_from_assignment


def _doc(session, n=1):
    d = Document(filename=f"a{n}.pdf", file_path=f"/tmp/a{n}.pdf", content_hash=f"alerts-{n}",
                 source=DocumentSource.MANUAL, status=DocumentStatus.PENDING, category="statement_positions")
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def _loan(**kw):
    base = dict(number="900100200", label="CREDITO HABITACAO", rate_percent=3.0, term_months=300,
                capital_granted=5000.0, start_date=None, capital_remaining=900.0, opening_balance=None,
                rows=[], next_due_date=date(2026, 9, 2), next_instalment_number=None, next_instalment=None,
                next_capital=None, next_interest=None, next_rate_percent=3.0,
                next_indexante_percent=1.8, next_spread_percent=1.2)
    base.update(kw)
    return ExtractedLoan(**base)


def _apply(session, as_of, n, **kw):
    apply_positions(session, _doc(session, n), ExtractedPositions(as_of=as_of, loans=[_loan(**kw)], funds=[], balances=[]))
    return session.exec(select(Debt)).first()


def _alerts(session, kind=None):
    q = select(LoanAlert).order_by(LoanAlert.id)
    rows = session.exec(q).all()
    return [a for a in rows if kind is None or a.kind == kind]


def _history_debt(session):
    return create_loan_from_assignment(session, "900100201", "L", "personal")


def _inst(n, capital, interest, month=None, bal=None):
    return Instalment(n, date(2026, month or n, 2), capital, interest, 0.0, bal)


# --- interest-only --------------------------------------------------------

def test_interest_only_series_raises_one_alert_each_and_is_idempotent(session):
    debt = _history_debt(session)
    series = [_inst(1, 0.0, 20.0), _inst(2, 0.0, 20.0), _inst(3, 0.0, 20.0), _inst(4, 100.0, 19.0, bal=900.0)]
    apply_loan_history(session, _doc(session), debt, series)
    alerts = _alerts(session, "interest_only")
    assert [a.ref for a in alerts] == ["inst:1", "inst:2", "inst:3"]
    assert all(a.debt_id == debt.id and not a.acknowledged for a in alerts)
    apply_loan_history(session, _doc(session, 2), debt, series)
    assert len(_alerts(session)) == 3


def test_interest_only_message_has_number_date_and_interest(session):
    debt = _history_debt(session)
    apply_loan_history(session, _doc(session), debt, [_inst(7, 0.0, 123.45, month=3)])
    msg = _alerts(session)[0].message
    assert "Instalment 7" in msg and "02 Mar 2026" in msg and "123.45" in msg and "interest only" in msg


def test_capital_and_interest_both_zero_raises_no_alert(session):
    debt = _history_debt(session)
    apply_loan_history(session, _doc(session), debt, [Instalment(1, date(2026, 1, 2), 0.0, 0.0, 5.0, None)])
    assert _alerts(session) == []


def test_normal_instalments_raise_no_alert(session):
    debt = _history_debt(session)
    apply_loan_history(session, _doc(session), debt, [_inst(1, 100.0, 20.0, bal=900.0)])
    assert _alerts(session) == []


def test_interest_only_in_a_statement_raises_alert(session):
    from app.services.position_extraction import ComponentRow
    rows = [ComponentRow(date(2026, 7, 2), 5, "interest", 30.0, None)]
    _apply(session, date(2026, 7, 31), 1, rows=rows)
    assert [a.ref for a in _alerts(session, "interest_only")] == ["inst:5"]


# --- spread ---------------------------------------------------------------

def test_baseline_spread_comes_from_the_first_snapshot_only(session):
    debt = _apply(session, date(2026, 7, 31), 1)
    assert debt.spread_percent == 1.2
    _apply(session, date(2026, 8, 31), 2, next_spread_percent=1.3, next_rate_percent=3.1)
    session.refresh(debt)
    assert debt.spread_percent == 1.2


def test_unchanged_spread_raises_no_alert(session):
    _apply(session, date(2026, 7, 31), 1)
    _apply(session, date(2026, 8, 31), 2)
    assert _alerts(session) == []


def test_spread_change_raises_alert_with_old_new_and_date(session):
    _apply(session, date(2026, 7, 31), 1)
    _apply(session, date(2026, 8, 31), 2, next_spread_percent=1.3, next_rate_percent=3.1)
    alerts = _alerts(session, "spread_changed")
    assert len(alerts) == 1 and alerts[0].ref == "snap:2026-08-31"
    assert "1.200" in alerts[0].message and "1.300" in alerts[0].message and "→" in alerts[0].message
    assert "31 Aug 2026" in alerts[0].message
    # applying the same statement again adds nothing
    _apply(session, date(2026, 8, 31), 3, next_spread_percent=1.3, next_rate_percent=3.1)
    assert len(_alerts(session)) == 1


def test_rate_inconsistent_alert_shows_the_three_numbers(session):
    _apply(session, date(2026, 7, 31), 1, next_rate_percent=3.5)
    alerts = _alerts(session, "rate_inconsistent")
    assert len(alerts) == 1 and alerts[0].ref == "snap:2026-07-31"
    assert "3.500" in alerts[0].message and "1.800" in alerts[0].message and "1.200" in alerts[0].message


def test_snapshot_without_the_three_numbers_raises_nothing(session):
    _apply(session, date(2026, 7, 31), 1, next_indexante_percent=None, next_spread_percent=None)
    debt = session.exec(select(Debt)).first()
    assert debt.spread_percent is None and _alerts(session) == []


def test_older_snapshot_arriving_later_becomes_the_baseline(session):
    _apply(session, date(2026, 9, 30), 1, next_spread_percent=1.3, next_rate_percent=3.1)
    debt = session.exec(select(Debt)).first()
    assert debt.spread_percent == 1.3 and _alerts(session) == []
    _apply(session, date(2026, 7, 31), 2)  # older, spread 1.2 -> new baseline
    session.refresh(debt)
    assert debt.spread_percent == 1.2
    alerts = _alerts(session, "spread_changed")
    assert [a.ref for a in alerts] == ["snap:2026-09-30"]


def test_baseline_change_deletes_unacknowledged_alerts_that_now_equal_it_but_keeps_acknowledged(session):
    _apply(session, date(2026, 7, 31), 1)  # 1.2 baseline
    _apply(session, date(2026, 8, 31), 2, next_spread_percent=1.3, next_rate_percent=3.1)  # Aug differs
    _apply(session, date(2026, 10, 31), 3, next_spread_percent=1.3, next_rate_percent=3.1)  # Oct differs
    assert sorted(a.ref for a in _alerts(session, "spread_changed")) == ["snap:2026-08-31", "snap:2026-10-31"]
    oct_alert = next(a for a in _alerts(session) if a.ref == "snap:2026-10-31")
    acknowledge_alert(session, oct_alert.id, "known")
    # an OLDER snapshot with 1.3 arrives: the baseline becomes 1.3
    _apply(session, date(2026, 6, 30), 4, next_spread_percent=1.3, next_rate_percent=3.1)
    debt = session.exec(select(Debt)).first()
    assert debt.spread_percent == 1.3
    refs = sorted(a.ref for a in _alerts(session, "spread_changed"))
    # Aug (unacknowledged, now equals the baseline) is gone; Oct (acknowledged) is kept;
    # Jul (1.2) now differs from the 1.3 baseline and is flagged.
    assert refs == ["snap:2026-07-31", "snap:2026-10-31"]
    assert next(a for a in _alerts(session) if a.ref == "snap:2026-10-31").acknowledged


# --- acknowledge ----------------------------------------------------------

def test_acknowledge_with_note(session):
    debt = _history_debt(session)
    apply_loan_history(session, _doc(session), debt, [_inst(1, 0.0, 20.0)])
    alert = _alerts(session)[0]
    out = acknowledge_alert(session, alert.id, "bank error, known")
    assert out.acknowledged and out.ack_note == "bank error, known" and out.acknowledged_at is not None
    again = acknowledge_alert(session, alert.id, "later note")
    assert again.ack_note == "bank error, known"  # first acknowledgement stands


def test_acknowledge_blank_note_is_none_and_unknown_id_raises(session):
    debt = _history_debt(session)
    apply_loan_history(session, _doc(session), debt, [_inst(1, 0.0, 20.0)])
    assert acknowledge_alert(session, _alerts(session)[0].id, "   ").ack_note is None
    with pytest.raises(LookupError):
        acknowledge_alert(session, 9999, None)


def test_acknowledged_alert_is_not_recreated_by_reapplying(session):
    debt = _history_debt(session)
    apply_loan_history(session, _doc(session), debt, [_inst(1, 0.0, 20.0)])
    acknowledge_alert(session, _alerts(session)[0].id, None)
    apply_loan_history(session, _doc(session, 2), debt, [_inst(1, 0.0, 20.0)])
    assert len(_alerts(session)) == 1 and _alerts(session)[0].acknowledged


# --- detection failure never fails the document ---------------------------

def test_failing_detector_keeps_history_and_logs(session, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("detector exploded")
    monkeypatch.setattr(position_store, "detect_alerts_for_instalments", boom)
    debt = _history_debt(session)
    with caplog.at_level(logging.ERROR):
        counts = apply_loan_history(session, _doc(session), debt, [_inst(1, 0.0, 20.0), _inst(2, 100.0, 19.0, bal=900.0)])
    assert counts["movements"] == 2
    assert len(session.exec(select(LoanMovement)).all()) == 2
    assert _alerts(session) == []
    assert "detector exploded" in caplog.text
    session.refresh(debt)
    assert float(debt.current_balance) == 900.0


def test_failing_snapshot_detector_keeps_positions(session, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("snapshot detector exploded")
    monkeypatch.setattr(position_store, "detect_alerts_for_snapshot", boom)
    debt = _apply(session, date(2026, 7, 31), 1)
    assert len(session.exec(select(LoanSnapshot)).all()) == 1
    assert float(debt.current_balance) == 900.0 and _alerts(session) == []
    # the session is still usable for the rest of the document
    _apply(session, date(2026, 8, 31), 2)
    assert len(session.exec(select(LoanSnapshot)).all()) == 2


@pytest.mark.asyncio
async def test_failing_detector_does_not_fail_the_document(session, tmp_path, monkeypatch):
    import json
    from app.services.position_store import process_loan_history_document
    from tests.fakes.fake_gateway import FakeGateway

    def boom(*a, **k):
        raise RuntimeError("nope")
    monkeypatch.setattr(position_store, "detect_alerts_for_instalments", boom)
    path = tmp_path / "h.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    doc = Document(filename="h.pdf", file_path=str(path), content_hash="h-fail", source=DocumentSource.MANUAL,
                   status=DocumentStatus.PENDING, category="loan_history")
    session.add(doc)
    session.commit()
    debt = _history_debt(session)
    apply_loan_history(session, _doc(session, 5), debt, [_inst(1, 100.0, 20.0, bal=900.0), _inst(2, 100.0, 19.0, bal=800.0)])
    rows = [{"date": f"2026-0{m}-02", "instalment_number": m, "component": c, "amount": a, "balance_after": b}
            for m, cs in ((1, (("capital", 100.0, 900.0), ("interest", 20.0, None))),
                          (2, (("capital", 100.0, 800.0), ("interest", 19.0, None))))
            for c, a, b in cs]
    gw = FakeGateway([{"text": json.dumps({"rows": rows}), "stop_reason": "end_turn",
                       "usage": {"input_tokens": 1, "output_tokens": 1}}])
    ext = await process_loan_history_document(session, doc, gateway=gw)
    assert ext.status == "ok"


# --- Overview needs-attention --------------------------------------------

def test_overview_one_item_per_loan_and_kind_group_and_none_after_ack(session):
    debt = _history_debt(session)
    debt.name = "Test loan"
    session.add(debt)
    session.commit()
    apply_loan_history(session, _doc(session), debt, [_inst(1, 0.0, 20.0), _inst(2, 0.0, 20.0)])
    items = [i for i in get_needs_attention(session, date(2026, 10, 6), []) if i.kind == "loan_alert"]
    assert len(items) == 1
    assert items[0].text.startswith("Test loan: 2 interest-only instalments (nº 1–2, total interest €40.00)")
    assert items[0].url == f"/financials/loans/{debt.id}"
    acknowledge_alert(session, _alerts(session)[0].id, None)
    items = [i for i in get_needs_attention(session, date(2026, 10, 6), []) if i.kind == "loan_alert"]
    assert len(items) == 1 and "Instalment 2" in items[0].text
    acknowledge_alert(session, _alerts(session)[1].id, None)
    assert [i for i in get_needs_attention(session, date(2026, 10, 6), []) if i.kind == "loan_alert"] == []


def test_fifteen_interest_only_alerts_make_one_overview_item(session):
    debt = _history_debt(session)
    debt.name = "Test loan"
    session.add(debt)
    session.commit()
    insts = [Instalment(n, date(2025 + (n - 1) // 12, (n - 1) % 12 + 1, 2), 0.0, 20.0, 0.0, None) for n in range(1, 16)]
    apply_loan_history(session, _doc(session), debt, insts)
    assert len(_alerts(session, "interest_only")) == 15  # individual alerts stay in the DB
    items = [i for i in get_needs_attention(session, date(2026, 10, 6), []) if i.kind == "loan_alert"]
    assert len(items) == 1
    assert "15 interest-only instalments (nº 1–15, total interest €300.00)" in items[0].text


def test_other_kinds_are_grouped_per_loan_with_count_and_latest_message(session):
    debt = _history_debt(session)
    debt.name = "Test loan"
    session.add(debt)
    for n, day in ((1, 1), (2, 2)):
        session.add(LoanAlert(debt_id=debt.id, kind="spread_changed", ref=f"snap:{n}", message=f"Spread changed #{n}",
                              detected_on=date(2026, 9, day)))
    session.commit()
    items = [i for i in get_needs_attention(session, date(2026, 10, 6), []) if i.kind == "loan_alert"]
    assert len(items) == 1 and "2 spread changes" in items[0].text and "Spread changed #2" in items[0].text


def test_ack_group_acknowledges_only_that_loan_and_kind(session):
    from app.services.loan_alerts import acknowledge_group
    a = _history_debt(session)
    b = create_loan_from_assignment(session, "900100202", "L2", "personal")
    session.add(LoanAlert(debt_id=a.id, kind="spread_changed", ref="s", message="x", detected_on=date(2026, 9, 1)))
    session.commit()
    apply_loan_history(session, _doc(session, 5), a, [_inst(1, 0.0, 20.0), _inst(2, 0.0, 20.0)])
    apply_loan_history(session, _doc(session, 6), b, [_inst(1, 0.0, 20.0)])
    assert acknowledge_group(session, a.id, "interest_only", "known") == 2
    rows = session.exec(select(LoanAlert)).all()
    done = {(r.debt_id, r.kind): r.acknowledged for r in rows}
    assert done[(a.id, "interest_only")] is True and done[(a.id, "spread_changed")] is False
    assert done[(b.id, "interest_only")] is False
    assert all(r.ack_note == "known" for r in rows if r.acknowledged)
