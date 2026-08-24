"""DB aggregation for the Overview screen: KPI cards, cash-flow chart data,
category comparison, narrative insight, and needs attention. Pure chart
math (auto-scaling, dual-direction bars) lives in overview_charts.py and
is imported here, not reimplemented."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models.transaction import Transaction, TransactionType
from app.services.overview_charts import TrendChart, build_trend_chart

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
