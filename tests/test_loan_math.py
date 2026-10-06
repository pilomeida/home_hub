from datetime import date
from decimal import Decimal

import pytest

from app.models.debt import Debt, DebtDirection, DebtKind
from app.models.document import Document, DocumentSource
from app.models.position import LoanMovement, LoanSnapshot
from app.models.transaction import Transaction, TransactionType
from app.services.loan_math import informal_balance, months_to_payoff, summarize_all, summarize_loan

TODAY = date(2026, 10, 6)


def _doc(session, n=1):
    d = Document(filename=f"d{n}.pdf", file_path=f"/tmp/d{n}.pdf", content_hash=f"lm-{n}", source=DocumentSource.MANUAL)
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def _loan(session, **kw):
    base = dict(kind=DebtKind.FORMAL, direction=DebtDirection.OWED_BY_US, original_amount=100000.0,
                current_balance=Decimal("0"), name="Loan A", external_number="1234567", status="active")
    base.update(kw)
    debt = Debt(**base)
    session.add(debt)
    session.commit()
    session.refresh(debt)
    return debt


def _mov(session, debt, doc, n, d, cap, intr, ins=0.0, bal=None):
    session.add(LoanMovement(debt_id=debt.id, instalment_number=n, movement_date=d, capital=cap,
                             interest=intr, insurance=ins, balance_after=bal, document_id=doc.id))
    session.commit()


def _simulate_months(balance, annual_rate, instalment):
    months = 0
    while balance > 1e-9:
        balance = balance * (1 + annual_rate / 1200) - instalment
        months += 1
        if months > 10000:
            return None
    return months


def test_months_to_payoff_matches_month_by_month_loop():
    expected = _simulate_months(100000.0, 3.0, 500.0)
    assert expected is not None
    assert months_to_payoff(100000, 3.0, 500) == expected


def test_months_to_payoff_zero_rate():
    assert months_to_payoff(1000, 0, 300) == 4


def test_months_to_payoff_instalment_below_interest_is_none():
    assert months_to_payoff(100000, 3.0, 200) is None  # interest is 250/month
    assert months_to_payoff(100000, 3.0, 250) is None
    assert months_to_payoff(100, 3.0, 0) is None


def test_months_to_payoff_nothing_owed_is_zero():
    assert months_to_payoff(0, 3.0, 500) == 0


def test_summarize_loan_uses_newest_snapshot_and_movement_totals(session):
    doc = _doc(session)
    debt = _loan(session, interest_rate=9.9, capital_granted=100000.0)
    session.add(LoanSnapshot(debt_id=debt.id, as_of=date(2026, 8, 1), capital_remaining=90000.0, rate_percent=3.5,
                             next_due_date=date(2026, 9, 1), next_instalment=520.0, document_id=doc.id))
    session.add(LoanSnapshot(debt_id=debt.id, as_of=date(2026, 9, 5), capital_remaining=80000.0, rate_percent=3.0,
                             next_due_date=date(2026, 10, 1), next_instalment=500.0, document_id=doc.id))
    session.commit()
    _mov(session, debt, doc, 1, date(2026, 7, 1), 300.0, 200.0, 12.0, 80500.0)
    _mov(session, debt, doc, 2, date(2026, 8, 1), 310.0, 190.0, 11.0, 80190.0)
    s = summarize_loan(session, debt, TODAY)
    assert s.capital_remaining == 80000.0
    assert s.balance_known is True
    assert s.rate_percent == 3.0
    assert s.as_of == date(2026, 9, 5)
    assert s.paid_capital_total == 610.0
    assert s.paid_interest_total == 390.0
    assert s.paid_insurance_total == 23.0
    assert s.last_insurance == 11.0
    assert s.next_due_date == date(2026, 10, 1)
    assert s.next_instalment == 500.0
    assert s.projected is False
    assert s.stale_days == 31
    assert s.capital_granted == 100000.0
    assert s.months_left == _simulate_months(80000.0, 3.0, 500.0)


def test_loan_without_snapshot_projects_from_latest_movement(session):
    doc = _doc(session)
    debt = _loan(session, name="Third", interest_rate=4.0, current_balance=Decimal("50000.00"))
    _mov(session, debt, doc, 1, date(2026, 8, 31), 200.0, 150.0, 9.0)
    _mov(session, debt, doc, 2, date(2026, 9, 30), 210.0, 149.0, 9.5)
    s = summarize_loan(session, debt, TODAY)
    assert s.capital_remaining == 50000.0
    assert s.balance_known is True
    assert s.projected is True
    assert s.rate_percent == 4.0
    assert s.as_of == date(2026, 9, 30)
    assert s.next_instalment == pytest.approx(359.0)
    assert s.next_due_date == date(2026, 10, 30)
    assert s.last_insurance == 9.5
    assert s.stale_days == 6
    n = _simulate_months(50000.0, 4.0, 359.0)
    assert s.months_left == n
    # payoff = next_due_date + (months_left - 1) months
    total = 2026 * 12 + 9 + (n - 1)
    assert (s.payoff_date.year, s.payoff_date.month) == (total // 12, total % 12 + 1)


def test_projected_due_date_clamps_day_of_month(session):
    doc = _doc(session)
    debt = _loan(session, current_balance=Decimal("1000"))
    _mov(session, debt, doc, 1, date(2026, 1, 31), 100.0, 5.0)
    assert summarize_loan(session, debt, TODAY).next_due_date == date(2026, 2, 28)


def test_printout_balance_prefers_movement_balance_after(session):
    doc = _doc(session)
    debt = _loan(session, current_balance=Decimal("999.00"))
    _mov(session, debt, doc, 1, date(2026, 8, 1), 100.0, 5.0, 0.0, 700.0)
    assert summarize_loan(session, debt, TODAY).capital_remaining == 700.0


def test_interest_only_history_has_unknown_balance(session):
    doc = _doc(session)
    debt = _loan(session, interest_rate=4.0)
    _mov(session, debt, doc, 1, date(2026, 8, 1), 0.0, 100.0, 5.0, None)
    s = summarize_loan(session, debt, TODAY)
    assert s.balance_known is False
    assert s.capital_remaining == 0.0
    assert s.payoff_date is None and s.months_left is None
    assert s.next_instalment == 100.0  # insurance excluded


def test_loan_without_any_data_is_unknown(session):
    debt = _loan(session)
    s = summarize_loan(session, debt, TODAY)
    assert s.balance_known is False
    assert s.as_of is None and s.stale_days is None and s.next_instalment is None


def test_summarize_all_active_first_excludes_informal(session):
    _loan(session, name="Zed", external_number="111")
    _loan(session, name="Alpha", external_number="222", status="closed")
    _loan(session, name="Beta", external_number="333")
    session.add(Debt(kind=DebtKind.INFORMAL, original_amount=10.0, current_balance=Decimal("10")))
    session.commit()
    assert [s.name for s in summarize_all(session, TODAY)] == ["Beta", "Zed", "Alpha"]


def _informal(session, direction):
    from app.services import debt_ledger
    d = debt_ledger.create_informal_debt(session, "Test Person", direction)
    session.commit()
    session.refresh(d)
    return d


def _entry(session, debt, kind, amount, day=1):
    from app.services import debt_ledger
    debt_ledger.add_entry(session, debt, kind, amount, date(2026, 1, day))
    session.commit()


def test_informal_balance_reads_the_ledger_owed_by_us(session):
    d = _informal(session, DebtDirection.OWED_BY_US)
    _entry(session, d, "advance", 1000.0, 1)
    _entry(session, d, "repayment", 300.0, 2)
    _entry(session, d, "adjust_down", 50.0, 3)
    assert informal_balance(session, d) == Decimal("650.00")


def test_informal_balance_owed_to_us_is_the_same_arithmetic(session):
    d = _informal(session, DebtDirection.OWED_TO_US)
    _entry(session, d, "advance", 1000.0, 1)
    _entry(session, d, "repayment", 300.0, 2)
    _entry(session, d, "adjust_up", 50.0, 3)
    assert informal_balance(session, d) == Decimal("750.00")


def test_informal_balance_never_negative(session):
    d = _informal(session, DebtDirection.OWED_BY_US)
    _entry(session, d, "advance", 100.0, 1)
    _entry(session, d, "repayment", 500.0, 2)
    assert informal_balance(session, d) == Decimal("0.00")


def _txn(session, debt, ttype, amount, n):
    doc = _doc(session, 100 + n)
    t = Transaction(document_id=doc.id, provider=f"P{n}", amount=amount, transaction_type=ttype, debt_id=debt.id)
    session.add(t)
    session.commit()


def test_linked_transactions_without_an_entry_do_not_count(session):
    # the old heuristic is gone: only ledger entries move the balance
    d = _informal(session, DebtDirection.OWED_TO_US)
    _entry(session, d, "advance", 3000.0, 1)
    _txn(session, d, TransactionType.CREDIT, 500.0, 2)
    assert informal_balance(session, d) == Decimal("3000.00")


# --- I2: effective (next-instalment) rate ---------------------------------

from app.services.loan_math import effective_rate  # noqa: E402


def test_effective_rate_fallback_order():
    assert effective_rate(3.5, 2.9, 100.0, 40000.0) == (3.5, "next")
    assert effective_rate(None, 2.9, 100.0, 40000.0) == (2.9, "applied")
    assert effective_rate(None, None, 100.0, 40000.0) == (3.0, "derived")  # 100 * 12 / 40000 = 3%
    assert effective_rate(None, None, None, 40000.0) == (None, None)
    assert effective_rate(None, None, 100.0, 0.0) == (None, None)


def test_payoff_uses_next_rate_not_applied_rate(session):
    doc = _doc(session)
    debt = _loan(session, interest_rate=2.0, capital_granted=60000.0)
    session.add(LoanSnapshot(debt_id=debt.id, as_of=date(2026, 9, 5), capital_remaining=40000.0, rate_percent=2.0,
                             next_rate_percent=3.5, next_due_date=date(2026, 10, 1), next_instalment=300.0,
                             document_id=doc.id))
    session.commit()
    s = summarize_loan(session, debt, TODAY)
    assert s.rate_percent == 3.5 and s.rate_source == "next" and s.applied_rate_percent == 2.0
    assert s.months_left == _simulate_months(40000.0, 3.5, 300.0)
    assert s.months_left != _simulate_months(40000.0, 2.0, 300.0)


def test_summary_without_next_rate_uses_applied_then_derived(session):
    doc = _doc(session)
    debt = _loan(session, interest_rate=None)
    session.add(LoanSnapshot(debt_id=debt.id, as_of=date(2026, 9, 5), capital_remaining=40000.0, rate_percent=2.0,
                             next_due_date=date(2026, 10, 1), next_instalment=300.0, document_id=doc.id))
    session.commit()
    s = summarize_loan(session, debt, TODAY)
    assert (s.rate_percent, s.rate_source, s.applied_rate_percent) == (2.0, "applied", None)
    debt2 = _loan(session, name="B", external_number="7654321", interest_rate=None)
    session.add(LoanSnapshot(debt_id=debt2.id, as_of=date(2026, 9, 5), capital_remaining=48000.0,
                             next_due_date=date(2026, 10, 1), next_instalment=300.0, next_capital=100.0,
                             next_interest=200.0, document_id=doc.id))
    session.commit()
    s2 = summarize_loan(session, debt2, TODAY)
    assert (s2.rate_percent, s2.rate_source) == (5.0, "derived")  # 200 * 12 / 48000


# --- I6: a statement is a complete picture ---------------------------------

from app.models.position import BalanceSnapshot, SavingsSnapshot  # noqa: E402
from app.services.loan_math import newest_balances, position_totals, savings_lines  # noqa: E402


def _bal(session, doc, kind, label, amount, as_of):
    session.add(BalanceSnapshot(as_of=as_of, kind=kind, label=label, amount=amount, document_id=doc.id))
    session.commit()


def _fund(session, doc, ref, label, value, as_of):
    session.add(SavingsSnapshot(as_of=as_of, holder="H", label=label, account_ref=ref, value=value,
                                document_id=doc.id))
    session.commit()


def test_relabelled_deposit_only_newest_statement_counts(session):
    doc = _doc(session)
    _bal(session, doc, "deposit", "A", 500.0, date(2026, 7, 31))
    _bal(session, doc, "deposit", "B", 700.0, date(2026, 9, 30))
    assert [b.label for b in newest_balances(session, "deposit")] == ["B"]
    assert position_totals(session, TODAY).deposits_total == 700.0


def test_card_only_document_does_not_hide_deposits(session):
    doc = _doc(session)
    _bal(session, doc, "deposit", "D", 800.0, date(2026, 9, 30))
    _bal(session, doc, "card", "CARD", 90.0, date(2026, 10, 3))
    assert [b.label for b in newest_balances(session, "deposit")] == ["D"]
    t = position_totals(session, TODAY)
    assert t.deposits_total == 800.0 and t.cards_total == 90.0


def test_card_missing_from_latest_card_bearing_statement_drops(session):
    doc = _doc(session)
    _bal(session, doc, "card", "OLD CARD", 90.0, date(2026, 7, 31))
    _bal(session, doc, "card", "NEW CARD", 20.0, date(2026, 9, 30))
    assert [b.label for b in newest_balances(session, "card")] == ["NEW CARD"]
    assert position_totals(session, TODAY).cards_total == 20.0


def test_fund_absent_from_newest_statement_disappears_and_refs_key_on_account(session):
    doc = _doc(session)
    _fund(session, doc, "111", "OLD FUND", 1000.0, date(2026, 7, 31))
    _fund(session, doc, "222", "FUND NAME V1", 2000.0, date(2026, 7, 31))
    _fund(session, doc, "222", "FUND NAME V2", 2100.0, date(2026, 9, 30))
    _fund(session, doc, "333", "OTHER", 300.0, date(2026, 9, 30))
    lines = savings_lines(session)
    assert sorted((l.label, l.value) for l in lines) == [("FUND NAME V2", 2100.0), ("OTHER", 300.0)]
    assert position_totals(session, TODAY).savings_total == 2400.0


def test_position_totals_and_overview_debt_kpi_read_the_ledger(session):
    from app.services.loan_math import position_totals
    from app.services.overview_service import get_debt_kpi
    lent = _informal(session, DebtDirection.OWED_TO_US)
    _entry(session, lent, "advance", 1000.0, 1)
    _entry(session, lent, "repayment", 300.0, 2)
    _entry(session, lent, "advance", 500.0, 3)  # they owe us 1200
    borrowed = _informal(session, DebtDirection.OWED_BY_US)
    _entry(session, borrowed, "advance", 2000.0, 1)
    _entry(session, borrowed, "repayment", 250.0, 2)  # we owe 1750
    today = date(2026, 10, 6)
    assert position_totals(session, today).debt_total == 550.0  # 1750 - 1200
    assert get_debt_kpi(session, today=today).value == 550.0
    _entry(session, borrowed, "repayment", 5000.0, 4)  # overpaid: floors at zero, does not go negative
    assert position_totals(session, today).debt_total == 0.0
