"""Loan maths: payoff projection, per-loan summaries read from statements and
printouts, and informal (person-to-person) debt balances read from the ledger."""

import calendar
import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.debt import Debt, DebtDirection
from app.models.position import BalanceSnapshot, DebtEntry, LoanMovement, LoanSnapshot, SavingsSnapshot


@dataclass
class LoanSummary:
    debt_id: int
    name: str
    loan_type: Optional[str]
    rate_percent: Optional[float]
    capital_granted: Optional[float]
    capital_remaining: float
    as_of: Optional[date]
    paid_capital_total: float
    paid_interest_total: float
    paid_insurance_total: float
    last_insurance: float
    next_due_date: Optional[date]
    next_instalment: Optional[float]  # capital + interest; insurance is debited separately
    projected: bool  # next_* derived from the latest movement, not a statement
    payoff_date: Optional[date]
    months_left: Optional[int]
    stale_days: Optional[int]
    balance_known: bool = True  # False: no snapshot and no movement balance; capital_remaining is 0.0
    rate_source: Optional[str] = None  # next | applied | derived | debt
    recorded_since: Optional[date] = None  # date of the earliest recorded instalment (paid_* totals start here)
    capital_repaid: Optional[float] = None  # granted - remaining; only when both are known
    applied_rate_percent: Optional[float] = None  # rate of the last instalment, only when it differs from rate_percent


def add_months(d: date, months: int) -> date:
    index = d.year * 12 + (d.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def months_to_payoff(balance: float, annual_rate_percent: float, instalment: float) -> Optional[int]:
    if balance is None or annual_rate_percent is None or instalment is None:
        return None
    if annual_rate_percent < 0 or instalment <= 0:
        return None
    if balance <= 0:
        return 0
    r = annual_rate_percent / 1200
    if r == 0:
        return math.ceil(balance / instalment)
    if instalment <= balance * r:
        return None
    n = -math.log(1 - balance * r / instalment) / math.log(1 + r)
    return math.ceil(n - 1e-9)


def effective_rate(next_rate: Optional[float], applied_rate: Optional[float],
                   next_interest: Optional[float], capital_remaining: Optional[float]):
    """The rate that governs the NEXT instalment, with its source: the statement's
    revised rate (next), else the applied rate, else next_interest * 12 / capital
    remaining (derived). (None, None) when nothing is known."""
    if next_rate is not None:
        return next_rate, "next"
    if applied_rate is not None:
        return applied_rate, "applied"
    if next_interest is not None and capital_remaining:
        return round(next_interest * 12 / capital_remaining * 100, 3), "derived"
    return None, None


def summarize_loan(session: Session, debt: Debt, today: date) -> LoanSummary:
    snapshot = session.exec(
        select(LoanSnapshot).where(LoanSnapshot.debt_id == debt.id).order_by(LoanSnapshot.as_of.desc())
    ).first()
    movements = session.exec(
        select(LoanMovement).where(LoanMovement.debt_id == debt.id)
        .order_by(LoanMovement.movement_date.desc(), LoanMovement.instalment_number.desc())
    ).all()
    latest = movements[0] if movements else None

    rate, rate_source, applied_rate = debt.interest_rate, ("debt" if debt.interest_rate is not None else None), None
    if snapshot is not None:
        snap_rate, snap_source = effective_rate(
            snapshot.next_rate_percent, snapshot.rate_percent, snapshot.next_interest, snapshot.capital_remaining)
        if snap_rate is not None:
            rate, rate_source = snap_rate, snap_source
        if snap_source == "next" and snapshot.rate_percent is not None and abs(snapshot.rate_percent - snap_rate) > 1e-9:
            applied_rate = snapshot.rate_percent
    dates = [d for d in (snapshot.as_of if snapshot else None, latest.movement_date if latest else None) if d]
    as_of = max(dates) if dates else None

    use_snapshot = snapshot is not None and (latest is None or snapshot.as_of >= latest.movement_date)
    remaining: Optional[float] = None
    next_due: Optional[date] = None
    next_inst: Optional[float] = None
    projected = False
    if use_snapshot:
        remaining = snapshot.capital_remaining
        next_due = snapshot.next_due_date
        next_inst = snapshot.next_instalment
        if next_inst is None and snapshot.next_capital is not None and snapshot.next_interest is not None:
            next_inst = snapshot.next_capital + snapshot.next_interest
    else:
        with_balance = next((m for m in movements if m.balance_after is not None), None)
        if with_balance is not None:
            remaining = with_balance.balance_after
        elif debt.current_balance is not None and Decimal(debt.current_balance) > 0:
            remaining = float(debt.current_balance)
        if latest is not None:
            projected = True
            next_inst = latest.capital + latest.interest
            next_due = add_months(latest.movement_date, 1)

    balance_known = remaining is not None
    months_left = None
    payoff = None
    if balance_known and rate is not None and next_inst:
        months_left = months_to_payoff(remaining, rate, next_inst)
        if months_left is not None and next_due is not None:
            payoff = add_months(next_due, max(months_left - 1, 0))

    return LoanSummary(
        debt_id=debt.id, name=debt.name or "Loan", loan_type=debt.loan_type, rate_percent=rate,
        capital_granted=debt.capital_granted,
        capital_remaining=float(remaining) if balance_known else 0.0,
        as_of=as_of,
        paid_capital_total=round(sum(m.capital for m in movements), 2),
        paid_interest_total=round(sum(m.interest for m in movements), 2),
        paid_insurance_total=round(sum(m.insurance for m in movements), 2),
        last_insurance=latest.insurance if latest else 0.0,
        next_due_date=next_due, next_instalment=next_inst, projected=projected,
        payoff_date=payoff, months_left=months_left,
        stale_days=(today - as_of).days if as_of else None,
        balance_known=balance_known,
        recorded_since=min((m.movement_date for m in movements), default=None),
        capital_repaid=(round(debt.capital_granted - float(remaining), 2)
                        if balance_known and debt.capital_granted else None),
        rate_source=rate_source, applied_rate_percent=applied_rate,
    )


def summarize_all(session: Session, today: date) -> list[LoanSummary]:
    """Every loan known from a statement or printout (has an external number);
    active first, then closed, each by name."""
    debts = session.exec(select(Debt).where(Debt.external_number.isnot(None))).all()
    debts.sort(key=lambda d: (d.status == "closed", (d.name or "").lower(), d.id))
    return [summarize_loan(session, d, today) for d in debts]


@dataclass
class InformalSummary:
    """The ledger of one informal debt. `balance` is never negative: an
    overpayment shows as `overpaid_by`. `entries` run oldest first with the
    signed running balance after each line."""
    advances_total: Decimal
    repayments_total: Decimal
    adjustments_net: Decimal  # adjust_up - adjust_down
    balance: Decimal
    overpaid_by: Decimal
    last_activity: Optional[date]
    entries: list  # [(DebtEntry, running_balance: Decimal)]


def _money(value) -> Decimal:
    return Decimal(str(round(float(value), 2))).quantize(Decimal("0.01"))


def informal_summary(session: Session, debt: Debt) -> InformalSummary:
    entries = session.exec(
        select(DebtEntry).where(DebtEntry.debt_id == debt.id).order_by(DebtEntry.entry_date, DebtEntry.id)
    ).all()
    zero = Decimal("0.00")
    advances = repayments = up = down = zero
    running, lines = zero, []
    for e in entries:
        amount = _money(e.amount)
        if e.kind == "advance":
            advances += amount
            running += amount
        elif e.kind == "adjust_up":
            up += amount
            running += amount
        elif e.kind == "repayment":
            repayments += amount
            running -= amount
        else:  # adjust_down
            down += amount
            running -= amount
        lines.append((e, running))
    return InformalSummary(
        advances_total=advances, repayments_total=repayments, adjustments_net=up - down,
        balance=max(running, zero), overpaid_by=max(-running, zero),
        last_activity=max((e.entry_date for e in entries), default=None), entries=lines,
    )


def informal_balance(session: Session, debt: Debt) -> Decimal:
    """The debt's balance from its ledger entries (never negative)."""
    return informal_summary(session, debt).balance


@dataclass
class SavingsLine:
    holder: Optional[str]
    label: str
    invested: Optional[float]
    value: float
    gain: Optional[float]
    as_of: date
    periodic_amount: Optional[float] = None
    next_periodic_date: Optional[date] = None


@dataclass
class PositionTotals:
    savings_total: float
    deposits_total: float
    debt_total: float
    cards_total: float
    net_position: float
    as_of: Optional[date]
    stale_days: Optional[int]


def savings_lines(session: Session) -> list[SavingsLine]:
    """Each statement is a complete picture: only the funds at the newest as_of,
    keyed on account_ref alone (the label is free text), by holder then label."""
    newest_date = session.exec(select(func.max(SavingsSnapshot.as_of))).one()
    if newest_date is None:
        return []
    newest: dict[str, SavingsSnapshot] = {}
    for snap in session.exec(
        select(SavingsSnapshot).where(SavingsSnapshot.as_of == newest_date).order_by(SavingsSnapshot.id)
    ).all():
        newest[snap.account_ref] = snap
    lines = [
        SavingsLine(
            holder=s.holder, label=s.label, invested=s.invested, value=s.value,
            gain=round(s.value - s.invested, 2) if s.invested is not None else None,
            as_of=s.as_of, periodic_amount=s.periodic_amount, next_periodic_date=s.next_periodic_date,
        )
        for s in newest.values()
    ]
    lines.sort(key=lambda l: ((l.holder or "").lower(), l.label.lower()))
    return lines


def newest_balances(session: Session, kind: str) -> list[BalanceSnapshot]:
    """Balances of one kind at THAT kind's newest as_of: a card missing from the
    latest card-bearing statement drops, while a kind absent from a newer
    document keeps its older value (the page's 'data as of' marker covers it)."""
    newest_date = session.exec(select(func.max(BalanceSnapshot.as_of)).where(BalanceSnapshot.kind == kind)).one()
    if newest_date is None:
        return []
    rows = session.exec(
        select(BalanceSnapshot).where(BalanceSnapshot.kind == kind, BalanceSnapshot.as_of == newest_date)
    ).all()
    return sorted(rows, key=lambda b: b.label.lower())


def informal_debt_rows(session: Session) -> list[tuple[Debt, Decimal]]:
    """Open debts without an external number (informal and legacy formal ones),
    with their stored current balance. ONE rule shared by the Loans page and the
    Overview debt card: a direction of None means owed by us."""
    debts = session.exec(
        select(Debt).where(Debt.external_number.is_(None), Debt.status == "active").order_by(Debt.id)
    ).all()
    return [(d, Decimal(d.current_balance)) for d in debts]


def legacy_debt_total(rows: list[tuple[Debt, Decimal]]) -> float:
    """Net owed by us: OWED_TO_US debts subtract, everything else (including a
    missing direction) adds."""
    return sum(-float(b) if d.direction == DebtDirection.OWED_TO_US else float(b) for d, b in rows)


def position_totals(session: Session, today: date) -> PositionTotals:
    """Net position from statements/printouts only; the bank's own loans_total
    balance is never used. Loans with an unknown balance are excluded."""
    lines = savings_lines(session)
    deposits = newest_balances(session, "deposit")
    cards = newest_balances(session, "card")
    loans = [l for l in summarize_all(session, today) if l.balance_known and l.capital_remaining > 0]
    debt = sum(l.capital_remaining for l in loans)
    debt += legacy_debt_total(informal_debt_rows(session))
    debt = max(round(debt, 2), 0.0)
    savings_total = round(sum(l.value for l in lines), 2)
    deposits_total = round(sum(b.amount for b in deposits), 2)
    cards_total = round(sum(b.amount for b in cards), 2)
    dates = [l.as_of for l in lines] + [b.as_of for b in deposits + cards]
    dates += [l.as_of for l in summarize_all(session, today) if l.as_of]
    as_of = max(dates) if dates else None
    return PositionTotals(
        savings_total=savings_total, deposits_total=deposits_total, debt_total=debt, cards_total=cards_total,
        net_position=round(savings_total + deposits_total - debt - cards_total, 2),
        as_of=as_of, stale_days=(today - as_of).days if as_of else None,
    )
