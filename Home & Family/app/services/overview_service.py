"""DB aggregation for the Overview screen: KPI cards, cash-flow chart data,
category comparison, narrative insight, and needs attention. Pure chart
math (auto-scaling, dual-direction bars) lives in overview_charts.py and
is imported here, not reimplemented."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models.account import Account, AccountType
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
