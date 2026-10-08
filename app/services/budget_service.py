"""Budgets: CRUD, aggregation of actuals from the category tree, and the data
behind the Overview 'Budget' section."""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.models.budget import Budget
from app.models.category_node import CategoryNode
from app.models.transaction import Transaction, TransactionType
from app.services.budget_forecast import (
    Range,
    income_month_end,
    income_year_end,
    month_end_estimate,
    yearly_due_now,
    typical_month,
    year_end_monthly,
    yearly_line_estimate,
    yearly_line_month_estimate,
)
from app.services.overview_charts import _complete_months_before
from app.models.debt import Debt
from app.services.loan_math import add_months, summarize_all
from app.services.loan_insurance import LOAN_INSURANCE_SLUGS
from app.services.taxonomy import flow_of


def set_budget(session: Session, node_id: int, year: int, amount: float,
               expected_month: Optional[int] = None) -> Optional[Budget]:
    node = session.get(CategoryNode, node_id)
    if node is None or node.level != 3 or node.kind != "out" or node.cadence not in ("monthly", "yearly"):
        raise ValueError("budgets can only be set on monthly or yearly outflow sub-categories")
    if expected_month is not None and (node.cadence != "yearly" or not 1 <= expected_month <= 12):
        raise ValueError("expected_month applies to yearly sub-categories only (1-12)")
    existing = session.exec(select(Budget).where(Budget.node_id == node_id, Budget.year == year)).first()
    if amount <= 0:
        has_earlier = session.exec(select(Budget).where(Budget.node_id == node_id, Budget.year < year)).first()
        if has_earlier:  # tombstone: 0 in this year means "no budget", overriding the carry-over
            row = existing or Budget(node_id=node_id, year=year, amount=0.0)
            row.amount, row.expected_month = 0.0, None
            session.add(row)
        elif existing:
            session.delete(existing)
        session.commit()
        return None
    budget = existing or Budget(node_id=node_id, year=year, amount=amount)
    budget.amount, budget.expected_month = amount, expected_month
    session.add(budget)
    session.commit()
    session.refresh(budget)
    return budget


def get_budgets(session: Session, year: int) -> dict[int, Budget]:
    """Real budgets for `year`; zero-amount tombstone rows are not budgets."""
    return {b.node_id: b for b in session.exec(
        select(Budget).where(Budget.year == year, Budget.amount > 0)).all()}


def get_effective_budgets(session: Session, year: int) -> dict[int, tuple[Budget, bool]]:
    """Per node, the budget in force for `year`: that year's row, else the most
    recent EARLIER year's row (carry-over, flagged True) so budgets survive 1 January."""
    result: dict[int, tuple[Budget, bool]] = {}
    earlier = session.exec(select(Budget).where(Budget.year < year).order_by(Budget.year)).all()
    for b in earlier:  # ascending, so the latest earlier year wins
        result[b.node_id] = (b, True)
    for b in session.exec(select(Budget).where(Budget.year == year)).all():
        if b.amount > 0:
            result[b.node_id] = (b, False)
        else:  # tombstone: explicitly no budget this year
            result.pop(b.node_id, None)
    return result


# --------------------------------------------------------------------------
# Aggregation: actuals from the category tree + forecasts for the Overview
# --------------------------------------------------------------------------

_HISTORY_MONTHS = 36
_RELEVANCE_MONTHS = 12
_RECURRING_INCOME = {"income.psi", "income.employment", "income.rental",
                     "income.benefits-refunds-from-the-state", "savings-investments.earnings"}
_STATUS_RANK = {"no_budget": 0, "ok": 1, "at_risk": 2, "over": 3}


@dataclass
class BudgetLine:
    """One level-3 outflow node. `budget` is the planned amount for the line's
    cadence as the user set it: per month for monthly lines, per year for yearly
    lines (month view) -- in year view monthly lines show budget * 12."""
    node_id: int
    name: str
    cadence: str  # "monthly" | "yearly"
    budget: Optional[float]
    expected_month: Optional[int]
    spent: float  # month view: spent this month; year view: spent YTD
    expected: float
    if_budget_respected: Optional[float]
    status: str
    budget_in_period: float = 0.0  # what this line adds to roll-up budget sums for the selected period
    inherited: bool = False  # budget carried over from an earlier year


@dataclass
class BudgetCategory:
    name: str
    lines: list[BudgetLine]
    spent: float
    budget: float
    expected: float
    status: str


@dataclass
class BudgetGroup:
    name: str
    categories: list[BudgetCategory]
    spent: float
    budget: float
    expected: float
    status: str


@dataclass
class BudgetOverview:
    view: str  # "month" | "year"
    income: Range
    income_received: float
    spend_expected: float
    spend_if_budget_respected: float
    spend_spent: float
    net: Range
    groups: list[BudgetGroup]
    unsorted_spent: float
    unsorted_received: float = 0.0  # credits not yet filed in the tree
    loan_instalments: float = 0.0  # expected, not yet paid, for the period
    loan_instalments_paid: float = 0.0  # linked loan debits already paid in the period
    has_budgets: bool = False  # any (possibly carried-over, possibly hidden) budget exists


def _key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _worst(statuses: list[str]) -> str:
    return max(statuses, key=_STATUS_RANK.__getitem__) if statuses else "no_budget"


def _window_start(today: date) -> date:
    """First day of the earliest month needed: the oldest of the 36 complete
    months before this one and January of the current year."""
    oldest = _complete_months_before(today, _HISTORY_MONTHS)[-1]
    return min(date(int(oldest[:4]), int(oldest[5:]), 1), date(today.year, 1, 1))


def _signed(txn: Transaction, flow: str) -> Optional[float]:
    t = txn.transaction_type
    if flow not in ("out", "in"):
        return None
    if t == TransactionType.TRANSFER:
        return txn.amount  # a transfer filed by hand under an out/in node counts as spend/income
    sign = 1.0 if (t == TransactionType.DEBIT) == (flow == "out") else -1.0
    return sign * txn.amount


def _load_buckets(session: Session, today: date, nodes: dict[int, CategoryNode]):
    """ONE query for all transactions with a paid_date in the window; returns
    ({node_id: {"YYYY-MM": signed amount}}, {"YYYY-MM": unsorted outflow},
    {"YYYY-MM": unsorted inflow})."""
    rows = session.exec(select(Transaction).where(
        Transaction.paid_date.is_not(None),
        Transaction.paid_date >= _window_start(today),
        Transaction.paid_date <= today,
    )).all()
    by_node: dict[int, dict[str, float]] = {}
    unsorted: dict[str, float] = {}
    unsorted_in: dict[str, float] = {}
    for t in rows:
        node = nodes.get(t.category_id) if t.category_id is not None else None
        flow = flow_of(t, node)
        key = _key(t.paid_date)
        if node is None or node.slug.startswith("unsorted"):
            if flow == "out" and t.transaction_type == TransactionType.DEBIT:
                unsorted[key] = unsorted.get(key, 0.0) + t.amount
            elif t.transaction_type == TransactionType.CREDIT:
                unsorted_in[key] = unsorted_in.get(key, 0.0) + t.amount
            continue
        amount = _signed(t, flow)
        if amount is None or node.cadence == "loan":
            continue
        bucket = by_node.setdefault(node.id, {})
        bucket[key] = bucket.get(key, 0.0) + amount
    return by_node, unsorted, unsorted_in


def _zero_filled(history: dict[str, float], today: date) -> dict[str, float]:
    """Fill missing months with 0.0 from the first month with data through the
    last complete month before today, so seasonal gaps count as zero."""
    if not history:
        return history
    filled = dict(history)
    for key in _complete_months_before(today, 12 * 4):
        if key >= min(history):
            filled.setdefault(key, 0.0)
    return filled


def _sum_year(history: dict[str, float], year: int) -> float:
    prefix = f"{year:04d}-"
    return sum(v for k, v in history.items() if k.startswith(prefix))


def _build_line(node: CategoryNode, history: dict[str, float], budget: Optional[Budget],
                today: date, view: str, inherited: bool = False) -> Optional[BudgetLine]:
    mtd = history.get(_key(today), 0.0)
    ytd = _sum_year(history, today.year)
    last_year = _sum_year(history, today.year - 1)
    amount = budget.amount if budget else None
    expected_month = budget.expected_month if budget else None
    if node.cadence == "monthly":
        typical = typical_month(history, today)
        if view == "month":
            est = month_end_estimate(mtd, typical, amount)
            spent, shown_budget = mtd, amount
        else:
            est = year_end_monthly(ytd, mtd, typical, amount, today)
            spent, shown_budget = ytd, None if amount is None else amount * 12
        expected, respected, status = est.expected, est.if_budget_respected, est.status
        in_period = 0.0 if amount is None else (amount if view == "month" else amount * 12)
    else:
        est = yearly_line_estimate(ytd, amount, last_year, expected_month, today)
        respected, status, shown_budget = est.if_budget_respected, est.status, amount
        in_period = 0.0
        if amount is not None:
            if view == "year":
                in_period = amount
            else:
                if yearly_due_now(ytd, expected_month, today):
                    in_period = max(0.0, amount - (ytd - mtd))
        if view == "month":
            expected = yearly_line_month_estimate(mtd, ytd, amount, last_year, expected_month, today)
            spent = mtd
            if spent <= 0 and expected <= 0:
                return None  # neither paid nor due this month
        else:
            expected, spent = est.expected, ytd
    return BudgetLine(node.id, node.name, node.cadence, shown_budget, expected_month,
                      spent, expected, respected, status, in_period, inherited)


def _rollup(items: list, name: str, cls):
    return cls(
        name, items,
        sum(i.spent for i in items), sum(getattr(i, "budget_in_period", i.budget) for i in items), sum(i.expected for i in items),
        _worst([i.status for i in items]),
    )


def _loan_instalments(session: Session, today: date, view: str) -> tuple[float, float]:
    """(expected still to pay, already paid) for statement/printout loans in the
    period. Paid = linked instalment DEBITs; they are filed under loan nodes, which the
    budget lines skip, so they never enter spend. Linked loan-insurance debits are NOT
    instalments (they are ordinary spend). Informal debts are excluded."""
    loan_ids = set(session.exec(select(Debt.id).where(Debt.external_number.isnot(None))).all())
    if not loan_ids:
        return 0.0, 0.0
    start = date(today.year, today.month, 1) if view == "month" else date(today.year, 1, 1)
    paid_rows = session.exec(select(Transaction).where(
        Transaction.debt_id.in_(loan_ids), Transaction.transaction_type == TransactionType.DEBIT,
        Transaction.paid_date.is_not(None), Transaction.paid_date >= start, Transaction.paid_date <= today,
    )).all()
    # Loan insurance debits are linked to their loan but are ordinary spend (their
    # nodes are monthly budget lines), not instalments: never count them here.
    insurance_nodes = set(session.exec(
        select(CategoryNode.id).where(CategoryNode.slug.in_(LOAN_INSURANCE_SLUGS))).all())
    paid_rows = [t for t in paid_rows if t.category_id not in insurance_nodes]
    paid = sum(t.amount for t in paid_rows)
    paid_this_month = {t.debt_id for t in paid_rows if (t.paid_date.year, t.paid_date.month) == (today.year, today.month)}
    expected = 0.0
    month_start = date(today.year, today.month, 1)
    for loan in summarize_all(session, today):
        if loan.debt_id not in loan_ids or not loan.next_instalment or loan.next_due_date is None:
            continue
        if session.get(Debt, loan.debt_id).status != "active":
            continue
        if loan.balance_known and loan.capital_remaining <= 0:
            continue  # nothing left to pay (balance unknown but instalment known still counts)
        # A statement can be months old: roll the due date forward by whole months
        # (clamping the day) until it reaches the current month, so the instalment
        # is expected until it is actually debited.
        due = loan.next_due_date
        k = 0
        while due < month_start:
            k += 1
            due = add_months(loan.next_due_date, k)
        if loan.payoff_date and (today.year, today.month) > (loan.payoff_date.year, loan.payoff_date.month):
            continue  # the projection says it is already paid off
        paid_now = loan.debt_id in paid_this_month
        if view == "month":
            if (due.year, due.month) == (today.year, today.month) and not paid_now:
                expected += loan.next_instalment
            continue
        if due.year > today.year:
            continue
        first = due.month + 1 if (paid_now and due.month == today.month) else due.month
        for m in range(first, 13):
            if loan.payoff_date and (today.year, m) > (loan.payoff_date.year, loan.payoff_date.month):
                break
            expected += loan.next_instalment
    return round(expected, 2), round(paid, 2)


def get_budget_overview(session: Session, today: date, view: str = "month") -> BudgetOverview:
    """Budget vs actuals vs forecast for the Overview.

    Actuals come from a single transaction query (paid_date in the 36 complete
    months before this one, plus the current year, up to `today`), bucketed per
    (node, month). A line is listed only if its level-3 outflow node (monthly or
    yearly cadence; loan and neutral nodes are never listed) has a budget this
    year or positive net spend in the last 12 months -- the 12 complete months
    before the current one plus the current month. Forecast maths lives in
    budget_forecast.py.
    """
    if view not in ("month", "year"):
        raise ValueError("view must be 'month' or 'year'")
    nodes = {n.id: n for n in session.exec(select(CategoryNode)).all()}
    by_node, unsorted, unsorted_in = _load_buckets(session, today, nodes)
    budgets = get_effective_budgets(session, today.year)
    recent = set(_complete_months_before(today, _RELEVANCE_MONTHS)) | {_key(today)}

    lines_by_cat: dict[int, list[BudgetLine]] = {}
    income = Range(0.0, 0.0, 0.0)
    received = 0.0
    for node in sorted(nodes.values(), key=lambda n: n.sort_order):
        if node.level != 3 or node.cadence == "loan":
            continue
        history = by_node.get(node.id, {})
        mtd = history.get(_key(today), 0.0)
        ytd = _sum_year(history, today.year)
        if node.kind == "in":
            if not history:
                continue
            got = mtd if view == "month" else ytd
            if node.slug.rsplit(".", 1)[0] in _RECURRING_INCOME:
                history = _zero_filled(history, today)
                est = (income_month_end(mtd, history, today) if view == "month"
                       else income_year_end(ytd, mtd, history, today))
            else:  # one-offs (gifts, refunds): only what has been received, never projected
                est = Range(got, got, got)
            income = Range(income.low + est.low, income.expected + est.expected, income.high + est.high)
            received += mtd if view == "month" else ytd
        elif node.kind == "out" and node.cadence in ("monthly", "yearly"):
            budget, inherited = budgets.get(node.id, (None, False))
            if budget is None and not any(history.get(k, 0.0) > 0 for k in recent):
                continue
            line = _build_line(node, history, budget, today, view, inherited)
            if line is not None:
                lines_by_cat.setdefault(node.parent_id, []).append(line)

    groups_by_id: dict[int, list[BudgetCategory]] = {}
    for cat_id, lines in lines_by_cat.items():
        cat = nodes[cat_id]
        groups_by_id.setdefault(cat.parent_id, []).append((cat.sort_order, _rollup(lines, cat.name, BudgetCategory)))
    groups: list[BudgetGroup] = []
    for group_id, cats in sorted(groups_by_id.items(), key=lambda kv: nodes[kv[0]].sort_order):
        categories = [c for _, c in sorted(cats, key=lambda x: x[0])]
        groups.append(_rollup(categories, nodes[group_id].name, BudgetGroup))

    all_lines = [l for g in groups for c in g.categories for l in c.lines]
    spend_expected = sum(l.expected for l in all_lines)
    unsorted_spent = (unsorted.get(_key(today), 0.0) if view == "month"
                      else _sum_year(unsorted, today.year))
    unsorted_received = (unsorted_in.get(_key(today), 0.0) if view == "month"
                         else _sum_year(unsorted_in, today.year))
    loan_expected, loan_paid = _loan_instalments(session, today, view)
    loans_total = loan_expected + loan_paid
    return BudgetOverview(
        view=view, income=income, income_received=received,
        spend_expected=spend_expected,
        spend_if_budget_respected=sum(
            l.if_budget_respected if l.if_budget_respected is not None else l.expected for l in all_lines),
        spend_spent=sum(l.spent for l in all_lines),
        net=Range(income.low - spend_expected - loans_total, income.expected - spend_expected - loans_total,
                  income.high - spend_expected - loans_total),
        loan_instalments=loan_expected, loan_instalments_paid=loan_paid,
        groups=groups, unsorted_spent=unsorted_spent,
        unsorted_received=unsorted_received, has_budgets=bool(budgets),
    )
