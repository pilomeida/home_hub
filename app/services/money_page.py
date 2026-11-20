"""Money (PLAN step 10d): one page with a headline row, the spending breakdown and its trend; tabs for Bills & due, Loans,
Utilities and All lines (their pages, unchanged); and the category history: any category at any level, a number of
months, as columns, a history line, a trendline or bars.

Presentation only: every figure comes from the code that already feeds the Overview, the budget, Coming up and the
loans page. Spending is counted line by line with the budget's own rule (`budget_service.spend_of`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models.category_node import CategoryNode
from app.models.debt import Debt
from app.models.merchant import Merchant
from app.models.transaction import Transaction
from app.services.budget_service import get_budget_overview, spend_of
from app.services.coming_up import coming_up
from app.services.home import available, money_this_month
from app.services.overview_charts import build_cash_flow_chart, cash_flow_items, same_day
from app.services.overview_service import (CategoryComparisonRow, _monthly_flow_totals, _same_day_flow_totals,
                                           get_category_comparison)
from app.ui.charts import Bar, Item

DUE_SOON_DAYS = 7
MONTH_CHOICES = (3, 6, 12, 24)
VIEWS = {"columns": "Columns", "line": "History line", "trend": "Trendline", "bars": "Bars"}
UNSORTED = "unsorted"


@dataclass
class Headline:
    label: str
    value: str
    note: str
    tone: str = ""                   # "" | "good" | "bad"
    link: str = ""


@dataclass
class MoneyPage:
    today: date
    headline: list[Headline]
    groups: list[CategoryComparisonRow]
    trend: list[Item]


def _eur(v: float, cents: bool = False) -> str:
    return f"€{v:,.2f}" if cents else f"€{v:,.0f}"


def headline(session: Session, today: date) -> list[Headline]:
    _, spending = money_this_month(session, today)
    budget = get_budget_overview(session, today, "month")
    spent_note = (f"{_eur(budget.spend_if_budget_respected)} if budgets are respected" if budget.has_budgets
                  else "no budget set")
    out = [Headline("Spent this month", _eur(spending.now), spent_note, link="/overview")]
    cash = available(session, today)
    out.append(Headline("Cash balance", _eur(cash.balance, True) if cash else "—",
                        f"the bank's balance of {cash.as_of.isoformat()}, plus lines since" if cash
                        else "no bank account read yet", link="/financials/bank/"))
    view = coming_up(session, today)
    soon = view.overdue + [i for m in view.months for i in m.items if i.when <= today + timedelta(days=DUE_SOON_DAYS)]
    late = len(view.overdue)
    out.append(Headline("Due soon", _eur(sum(i.amount for i in soon), True),
                        (f"{len(soon)} in the next {DUE_SOON_DAYS} days" if soon else f"nothing in the next {DUE_SOON_DAYS} days")
                        + (f", {late} overdue" if late else ""), "bad" if late else "", "/financials/coming-up"))
    from app.services.loan_alerts import unacknowledged_groups
    loans = session.exec(select(Debt).where(Debt.external_number.is_not(None), Debt.status == "active")).all()
    flags = unacknowledged_groups(session)
    out.append(Headline("Loans", f"{len(loans)} open",
                        f"{len(flags)} red flag{'s' if len(flags) != 1 else ''}: {flags[0].debt.name or 'a loan'}" if flags
                        else "no red flag", "bad" if flags else "", "/financials/loans"))
    return out


def spending_trend(session: Session, today: date) -> list[Item]:
    """The last 12 months of spending: each past month in full with its part by the same day, this month so far."""
    income, expense = _monthly_flow_totals(session, today)
    income_by, expense_by = _same_day_flow_totals(session, today)
    chart = build_cash_flow_chart(income, expense, "12m", today, income_by, expense_by)
    return cash_flow_items(chart, "expense")


def money_page(session: Session, today: Optional[date] = None) -> MoneyPage:
    today = today or date.today()
    return MoneyPage(today, headline(session, today), get_category_comparison(session, today),
                     spending_trend(session, today))


# --- category history ------------------------------------------------------------------------------------------

@dataclass
class HistoryMonth:
    key: str                         # "2026-10"
    spent: float                     # the whole month (this month: so far)
    by_same_day: float
    current: bool
    top_merchant: Optional[str]
    lines_url: str


@dataclass
class CategoryHistory:
    slug: str
    label: str                       # "Food › Groceries"
    months: list[HistoryMonth] = field(default_factory=list)

    @property
    def items(self) -> list[Item]:
        return [Item(_label(m.key, len(self.months)), round(m.by_same_day, 2), current=True) if m.current else
                Item(_label(m.key, len(self.months)), round(m.by_same_day, 2), full=round(m.spent, 2))
                for m in self.months]

    @property
    def bars(self) -> list[Bar]:
        """One bar per month, newest first: solid = by the same day (this month: so far), pale track = whole month."""
        n = len(self.months)
        return [Bar(_label(m.key, n) + (" (so far)" if m.current else ""), round(m.by_same_day, 2),
                    0.0 if m.current else round(m.spent, 2), m.lines_url) for m in reversed(self.months)]

    @property
    def full_months(self) -> list[HistoryMonth]:
        return [m for m in self.months if not m.current]

    @property
    def average(self) -> Optional[float]:
        done = self.full_months
        return round(sum(m.spent for m in done) / len(done), 2) if done else None

    @property
    def highest(self) -> Optional[HistoryMonth]:
        done = self.full_months
        return max(done, key=lambda m: m.spent) if done else None


def _label(key: str, n: int) -> str:
    d = date(int(key[:4]), int(key[5:]), 1)
    return d.strftime("%b") if n <= 12 else d.strftime("%b %y")


def _months_back(today: date, n: int) -> list[str]:
    y, m, out = today.year, today.month, []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


def category_choices(session: Session) -> list[tuple[str, str]]:
    """(slug, label) of every spending category at every level, in tree order, plus "not filed yet"."""
    nodes = session.exec(select(CategoryNode).where(CategoryNode.kind == "out")).all()
    by_id = {n.id: n for n in nodes}

    def path(n: CategoryNode) -> list[CategoryNode]:
        out = [n]
        while out[0].parent_id in by_id:
            out.insert(0, by_id[out[0].parent_id])
        return out

    rows = []
    for n in nodes:
        p = path(n)
        rows.append(([(x.sort_order, x.name) for x in p], n.slug, " › ".join(x.name for x in p)))
    rows.sort()
    return [(UNSORTED, "Not filed yet")] + [(slug, label) for _, slug, label in rows if not slug.startswith(UNSORTED)]


def category_history(session: Session, slug: str, months: int, today: Optional[date] = None) -> Optional[CategoryHistory]:
    """Spending in one category (and everything under it) month by month. None for a category that doesn't exist."""
    today = today or date.today()
    labels = dict(category_choices(session))
    if slug not in labels:
        return None
    nodes = {n.id: n for n in session.exec(select(CategoryNode)).all()}

    def inside(node: Optional[CategoryNode]) -> bool:
        if node is None or node.slug.startswith(UNSORTED):
            return slug == UNSORTED
        return node.slug == slug or node.slug.startswith(slug + ".")

    keys = _months_back(today, months)
    start = date(int(keys[0][:4]), int(keys[0][5:]), 1)
    full: dict[str, float] = {}
    by_day: dict[str, float] = {}
    merchants: dict[str, dict[int, float]] = {}
    for t in session.exec(select(Transaction).where(Transaction.counts(), Transaction.paid_date >= start,
                                                    Transaction.paid_date <= today)):
        node = nodes.get(t.category_id) if t.category_id is not None else None
        if not inside(node):
            continue
        amount = spend_of(t, node)
        if amount is None:
            continue
        key = f"{t.paid_date.year:04d}-{t.paid_date.month:02d}"
        full[key] = full.get(key, 0.0) + amount
        if t.paid_date <= same_day(key, today):
            by_day[key] = by_day.get(key, 0.0) + amount
        if t.merchant_id is not None:
            merchants.setdefault(key, {})
            merchants[key][t.merchant_id] = merchants[key].get(t.merchant_id, 0.0) + amount
    names = {m.id: m.canonical_name for m in session.exec(select(Merchant).where(
        Merchant.id.in_({i for d in merchants.values() for i in d})))} if merchants else {}
    current = keys[-1]
    history = CategoryHistory(slug, labels[slug])
    for k in keys:
        top = max(merchants.get(k, {}).items(), key=lambda kv: kv[1], default=None)
        first = date(int(k[:4]), int(k[5:]), 1)
        last = today if k == current else _month_end(first)
        history.months.append(HistoryMonth(
            k, round(full.get(k, 0.0), 2), round(by_day.get(k, 0.0), 2), k == current,
            names.get(top[0]) if top and top[1] > 0 else None,
            f"/financials/transactions?category_node={slug}&date_from={first.isoformat()}&date_to={last.isoformat()}"))
    return history


def _month_end(first: date) -> date:
    nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


__all__ = ["MoneyPage", "Headline", "money_page", "headline", "spending_trend", "CategoryHistory", "HistoryMonth",
           "category_history", "category_choices", "MONTH_CHOICES", "VIEWS"]
