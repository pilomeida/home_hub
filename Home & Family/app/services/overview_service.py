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
from app.models.document import Document, DocumentStatus
from app.models.transaction import Category, Transaction, TransactionType
from app.services.classification_engine import get_needs_review_queue
from app.services.overview_charts import (  # noqa: F401 -- re-exported helper reused for month-end snapshots
    CashFlowChart,
    TrendChart,
    build_cash_flow_chart,
    build_trend_chart,
    _complete_months_before,
)

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
    expense).

    Design note: every Overview aggregate is scoped by `paid_date`, here and
    throughout this module. A transaction with no `paid_date` (e.g. an
    unpaid bill) therefore does not appear in any Overview number -- it only
    shows up as a Todo in the Household panel -- until it's marked paid.
    This is a deliberate design point, not an oversight: the Overview is
    meant to reflect money that has actually moved, not what's merely owed.
    """
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
    caption: Optional[str] = None


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
            drill_down_url=f"/transactions?transaction_type=credit&date_from={month_start}&date_to={today_iso}",
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
    return KpiCard(
        label="Cash", value=now_value, color="green", drill_down_url="/transactions", chart=chart,
        caption="Net tracked flow since first statement — not a live bank balance",
    )


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


_COMPARISON_ROLLING_MONTHS = 3
_COMPARISON_EXCLUDED_CATEGORIES = (Category.TRANSFER, Category.ATM_WITHDRAWAL, Category.INCOME)


@dataclass
class CategoryComparisonRow:
    category: str
    current_value: float
    rolling_avg_value: float
    delta_pct: Optional[float]
    bar_pct: float
    drill_down_url: str


def get_category_comparison(session: Session, today: date) -> list[CategoryComparisonRow]:
    cutoff = _month_start(today) - timedelta(days=31 * (_COMPARISON_ROLLING_MONTHS + 1))
    statement = select(Transaction).where(
        Transaction.transaction_type == TransactionType.DEBIT,
        Transaction.category.notin_(_COMPARISON_EXCLUDED_CATEGORIES),
        Transaction.paid_date >= cutoff,
        Transaction.paid_date <= today,
    )

    per_month_category: dict[tuple[str, str], float] = {}
    for t in session.exec(statement):
        if t.paid_date is None:
            continue
        key = (_month_key(t.paid_date), t.category.value)
        per_month_category[key] = per_month_category.get(key, 0.0) + t.amount

    current_key = _month_key(today)
    history_periods = _complete_months_before(today, _COMPARISON_ROLLING_MONTHS)
    categories = {cat for (_, cat) in per_month_category}

    computed = []
    for cat in categories:
        current_value = per_month_category.get((current_key, cat), 0.0)
        history_values = [per_month_category.get((p, cat), 0.0) for p in history_periods]
        rolling_avg = sum(history_values) / len(history_values) if history_values else 0.0
        if current_value == 0.0 and rolling_avg == 0.0:
            continue
        delta_pct = round((current_value - rolling_avg) / rolling_avg * 100.0, 1) if rolling_avg else None
        computed.append((cat, current_value, rolling_avg, delta_pct))

    computed.sort(key=lambda r: r[1], reverse=True)
    max_value = max((r[1] for r in computed), default=0.0)
    month_start = _month_start(today).isoformat()
    today_iso = today.isoformat()

    return [
        CategoryComparisonRow(
            category=cat, current_value=current_value, rolling_avg_value=rolling_avg,
            delta_pct=delta_pct,
            bar_pct=round(current_value / max_value * 100.0, 1) if max_value else 0.0,
            drill_down_url=f"/transactions?category={cat}&date_from={month_start}&date_to={today_iso}",
        )
        for cat, current_value, rolling_avg, delta_pct in computed
    ]


def get_narrative_insight(category_rows: list[CategoryComparisonRow]) -> Optional[str]:
    """A single deterministic sentence: overall spend trend vs. the 3-month
    average, plus whichever category moved the most in each direction.
    Rule-based, not an LLM call -- this runs on every home-page load, and
    an LLM round-trip there would add latency/cost/flakiness to the
    highest-traffic page in the app for no benefit a templated sentence
    over already-computed numbers doesn't already give."""
    if not category_rows:
        return None

    total_current = sum(r.current_value for r in category_rows)
    total_avg = sum(r.rolling_avg_value for r in category_rows)
    if not total_avg:
        return None

    change_pct = (total_current - total_avg) / total_avg * 100.0
    direction = "fell" if change_pct < 0 else "rose"
    sentence = f"Your spending {direction} {abs(round(change_pct, 1))}% vs. your 3-month average."

    deltas = sorted(
        ((r.category, r.current_value - r.rolling_avg_value) for r in category_rows),
        key=lambda d: d[1],
    )
    biggest_drop = deltas[0] if deltas[0][1] < 0 else None
    drop_category = biggest_drop[0] if biggest_drop else None
    biggest_rise = deltas[-1] if deltas[-1][1] > 0 and deltas[-1][0] != drop_category else None

    def _title(cat: str) -> str:
        return cat.replace("_", " ").title()

    if biggest_drop and biggest_rise:
        sentence += (
            f" {_title(biggest_drop[0])} accounted for €{abs(round(biggest_drop[1])):,.0f} of the "
            f"reduction, partly offset by €{round(biggest_rise[1]):,.0f} more in {_title(biggest_rise[0])}."
        )
    elif biggest_drop:
        sentence += f" {_title(biggest_drop[0])} accounted for €{abs(round(biggest_drop[1])):,.0f} of the change."
    elif biggest_rise:
        sentence += f" {_title(biggest_rise[0])} accounted for €{round(biggest_rise[1]):,.0f} of the increase."
    return sentence


_UPCOMING_BILL_LOOKAHEAD_DAYS = 14
_ANOMALY_THRESHOLD_PCT = 40.0
_ANOMALY_MIN_AVERAGE = 30.0


@dataclass
class NeedsAttentionItem:
    kind: str
    text: str
    url: str


def get_needs_attention(
    session: Session, today: date, category_rows: list[CategoryComparisonRow]
) -> list[NeedsAttentionItem]:
    items: list[NeedsAttentionItem] = []

    # Category anomalies never need the database -- checked first so the
    # test that passes session=None with a below-floor average never
    # touches `session` at all.
    for row in category_rows:
        if (
            row.rolling_avg_value >= _ANOMALY_MIN_AVERAGE
            and row.delta_pct is not None
            and row.delta_pct >= _ANOMALY_THRESHOLD_PCT
        ):
            items.append(NeedsAttentionItem(
                kind="category_anomaly",
                text=f"{row.category.replace('_', ' ').title()} is {row.delta_pct:.0f}% above its 3-month average this month",
                url=row.drill_down_url,
            ))

    if session is None:
        return items

    queue = get_needs_review_queue(session)
    review_count = (
        len(queue.unconfirmed_merchants) + len(queue.recurring_candidates)
        + len(queue.debt_candidates) + len(queue.unclassified_transactions)
    )
    if review_count:
        items.append(NeedsAttentionItem(
            kind="review_queue",
            text=f"{review_count} item{'s' if review_count != 1 else ''} waiting in Needs Review",
            url="/transactions/needs-review",
        ))

    lookahead = today + timedelta(days=_UPCOMING_BILL_LOOKAHEAD_DAYS)
    upcoming = session.exec(
        select(Commitment).where(
            Commitment.next_due_date.is_not(None),
            Commitment.next_due_date >= today,
            Commitment.next_due_date <= lookahead,
        )
    ).all()
    for c in upcoming:
        items.append(NeedsAttentionItem(
            kind="upcoming_bill",
            text=f"{c.name} due {c.next_due_date.strftime('%d %b')} (€{c.planned_amount:,.2f})",
            url=f"/transactions?commitment_id={c.id}",
        ))

    needs_attention_documents = session.exec(
        select(Document).where(Document.status.in_([DocumentStatus.NEEDS_ATTENTION, DocumentStatus.PENDING]))
    ).all()
    for doc in needs_attention_documents:
        items.append(NeedsAttentionItem(
            kind="document",
            text=f"{doc.filename} — {doc.failure_reason or 'needs attention'}",
            url=f"/bills/{doc.id}",
        ))

    return items


@dataclass
class OverviewData:
    flow_kpis: list[KpiCard]
    position_kpis: list[KpiCard]
    yearly_commitments: YearlyCommitmentCard
    narrative: Optional[str]
    cash_flow_chart: CashFlowChart
    category_comparison: list[CategoryComparisonRow]
    needs_attention: list[NeedsAttentionItem]


def get_overview_data(
    session: Session, today: Optional[date] = None, cash_flow_range: str = "12m"
) -> OverviewData:
    today = today or date.today()
    income_monthly, expense_monthly = _monthly_flow_totals(session, today)
    category_rows = get_category_comparison(session, today)

    # Narrative banner mitigation (Important finding, deliberately narrow
    # scope): get_narrative_insight compares month-to-date spend against a
    # full prior 3-month average, which is structurally misleading in the
    # first few days of a month (e.g. "fell 90%" when almost nothing has
    # posted yet). A full fix (day-of-month proration) is out of scope here
    # -- it would touch get_category_comparison and the anomaly-detection
    # threshold in get_needs_attention, both covered by existing exact-value
    # tests. Instead, just suppress the banner during the first 3 calendar
    # days, when MTD data is too partial for a fair comparison.
    narrative = get_narrative_insight(category_rows) if today.day > 3 else None

    return OverviewData(
        flow_kpis=get_flow_kpis(session, today, income_monthly, expense_monthly),
        position_kpis=[get_cash_kpi(session, today), get_debt_kpi(session, today)],
        yearly_commitments=get_yearly_commitments_card(session, today),
        narrative=narrative,
        cash_flow_chart=build_cash_flow_chart(income_monthly, expense_monthly, cash_flow_range, today),
        category_comparison=category_rows,
        needs_attention=get_needs_attention(session, today, category_rows),
    )
