"""DB aggregation for the Overview screen: KPI cards, cash-flow chart data,
category comparison, narrative insight, and needs attention. Pure chart
math (auto-scaling, dual-direction bars) lives in overview_charts.py and
is imported here, not reimplemented."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models.account import Account, AccountType
from app.models.commitment import Cadence, Commitment
from app.models.debt import Debt, DebtDirection, DebtKind
from app.models.transaction import Transaction, TransactionType
from app.services.overview_charts import TrendChart, build_trend_chart, _complete_months_before  # noqa: F401 -- re-exported helper reused for month-end snapshots

_TREND_MONTHS_BACK = 24


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _monthly_flow_totals(session: Session, today: date) -> tuple[dict[str, float], dict[str, float]]:
    """Buckets CREDIT/DEBIT transaction amounts by calendar month (paid_date),
    covering the trailing _TREND_MONTHS_BACK complete months plus the
    current in-progress month. TRANSFER-type rows are excluded (money
    moving between our own tracked accounts is neither income nor
    expense)."""
    cutoff = _month_start(today) - timedelta(days=31 * (_TREND_MONTHS_BACK + 1))
    statement = select(Transaction).where(
        Transaction.paid_date >= cutoff, Transaction.paid_date <= today,
    )
    income: dict[str, float] = {}
    expense: dict[str, float] = {}
    for t in session.exec(statement):
        if t.paid_date is None:
            continue
        key = _month_key(t.paid_date)
        if t.transaction_type == TransactionType.CREDIT:
            income[key] = income.get(key, 0.0) + t.amount
        elif t.transaction_type == TransactionType.DEBIT:
            expense[key] = expense.get(key, 0.0) + t.amount
    return income, expense


def _mtd_and_complete(monthly: dict[str, float], today: date) -> tuple[float, dict[str, float]]:
    current_key = _month_key(today)
    mtd = monthly.get(current_key, 0.0)
    complete = {k: v for k, v in monthly.items() if k != current_key}
    return mtd, complete


@dataclass
class KpiCard:
    label: str
    value: float
    color: str  # "green" or "red"
    drill_down_url: str
    chart: TrendChart


def get_flow_kpis(
    session: Session,
    today: date,
    income_monthly: dict[str, float],
    expense_monthly: dict[str, float],
) -> list[KpiCard]:
    income_mtd, income_complete = _mtd_and_complete(income_monthly, today)
    expense_mtd, expense_complete = _mtd_and_complete(expense_monthly, today)

    all_periods = set(income_complete) | set(expense_complete)
    net_complete = {p: income_complete.get(p, 0.0) - expense_complete.get(p, 0.0) for p in all_periods}
    net_mtd = income_mtd - expense_mtd

    month_start = _month_start(today).isoformat()
    today_iso = today.isoformat()

    return [
        KpiCard(
            label="Income", value=income_mtd, color="green",
            drill_down_url=f"/transactions?category=income&date_from={month_start}&date_to={today_iso}",
            chart=build_trend_chart(income_complete, today, income_mtd),
        ),
        KpiCard(
            label="Expenses", value=expense_mtd, color="red",
            drill_down_url=f"/transactions?transaction_type=debit&date_from={month_start}&date_to={today_iso}",
            chart=build_trend_chart(expense_complete, today, expense_mtd),
        ),
        KpiCard(
            label="Net flow", value=net_mtd, color="green",
            drill_down_url=f"/transactions?date_from={month_start}&date_to={today_iso}",
            chart=build_trend_chart(net_complete, today, net_mtd),
        ),
    ]


_CASH_ACCOUNT_TYPES = (AccountType.CHECKING, AccountType.SAVINGS, AccountType.WALLET)


def _cash_account_ids(session: Session) -> list[int]:
    accounts = session.exec(select(Account).where(Account.account_type.in_(_CASH_ACCOUNT_TYPES))).all()
    return [a.id for a in accounts]


def _cash_transactions(session: Session, account_ids: list[int]) -> list[Transaction]:
    if not account_ids:
        return []
    return session.exec(select(Transaction).where(Transaction.account_id.in_(account_ids))).all()


def _cash_balance_as_of(transactions: list[Transaction], as_of: date) -> float:
    """Cumulative net (CREDIT - DEBIT) across every transaction dated on or
    before `as_of`. This is a tracked net cash flow since ingestion began,
    not a live bank balance -- see plan Ruling R1."""
    balance = 0.0
    for t in transactions:
        if t.paid_date is None or t.paid_date > as_of:
            continue
        if t.transaction_type == TransactionType.CREDIT:
            balance += t.amount
        elif t.transaction_type == TransactionType.DEBIT:
            balance -= t.amount
        # TRANSFER rows excluded: money moving between our own tracked
        # accounts nets to zero across the cash pool as a whole.
    return balance


def _month_end(period: str) -> date:
    year, month = (int(p) for p in period.split("-"))
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def get_cash_kpi(session: Session, today: date) -> KpiCard:
    account_ids = _cash_account_ids(session)
    transactions = _cash_transactions(session, account_ids)
    now_value = _cash_balance_as_of(transactions, today)

    monthly = {
        period: _cash_balance_as_of(transactions, _month_end(period))
        for period in _complete_months_before(today, _TREND_MONTHS_BACK)
        if any(t.paid_date and t.paid_date <= _month_end(period) for t in transactions)
    }
    chart = build_trend_chart(monthly, today, now_value)
    return KpiCard(label="Cash", value=now_value, color="green", drill_down_url="/transactions", chart=chart)


def _debt_net_position(session: Session) -> float:
    debts = session.exec(select(Debt)).all()
    total = 0.0
    for d in debts:
        balance = float(d.current_balance)
        if d.kind == DebtKind.FORMAL or d.direction == DebtDirection.OWED_BY_US:
            total += balance
        elif d.direction == DebtDirection.OWED_TO_US:
            total -= balance
    return max(0.0, total)  # Ruling R10


def get_debt_kpi(session: Session, today: date) -> KpiCard:
    value = _debt_net_position(session)
    # No balance-history tracking exists for Debt yet (Ruling R2) -- an
    # empty monthly dict makes build_trend_chart render its already-tested
    # "no history yet" empty state, no special-casing needed here.
    chart = build_trend_chart({}, today, value)
    return KpiCard(label="Debt", value=value, color="red", drill_down_url="/transactions/needs-review", chart=chart)


@dataclass
class YearlyCommitmentCard:
    planned_total: float
    actual_total: float
    pct_of_plan: Optional[float]
    pct_of_year_elapsed: float
    next_item_label: Optional[str]
    next_item_date: Optional[date]
    next_item_url: Optional[str]
    drill_down_url: str
    has_commitments: bool


def get_yearly_commitments_card(session: Session, today: date) -> YearlyCommitmentCard:
    year = today.year
    commitments = session.exec(
        select(Commitment).where(Commitment.cadence == Cadence.YEARLY, Commitment.year == year)
    ).all()
    planned_total = sum(c.planned_amount for c in commitments)

    commitment_ids = [c.id for c in commitments]
    actual_total = 0.0
    if commitment_ids:
        linked = session.exec(select(Transaction).where(Transaction.commitment_id.in_(commitment_ids))).all()
        actual_total = sum(t.amount for t in linked)

    day_of_year = (today - date(year, 1, 1)).days + 1
    days_in_year = (date(year + 1, 1, 1) - date(year, 1, 1)).days
    pct_of_year_elapsed = round(day_of_year / days_in_year * 100.0, 1)
    pct_of_plan = round(actual_total / planned_total * 100.0, 1) if planned_total else None

    upcoming = sorted(
        (c for c in commitments if c.next_due_date and c.next_due_date >= today),
        key=lambda c: c.next_due_date,
    )
    next_commitment = upcoming[0] if upcoming else None

    return YearlyCommitmentCard(
        planned_total=planned_total,
        actual_total=actual_total,
        pct_of_plan=pct_of_plan,
        pct_of_year_elapsed=pct_of_year_elapsed,
        next_item_label=next_commitment.name if next_commitment else None,
        next_item_date=next_commitment.next_due_date if next_commitment else None,
        next_item_url=f"/transactions?commitment_id={next_commitment.id}" if next_commitment else None,
        drill_down_url=f"/transactions?date_from={date(year, 1, 1).isoformat()}&date_to={date(year, 12, 31).isoformat()}",
        has_commitments=bool(commitments),
    )
