"""Home (PLAN step 10c): the Ask box, Money this month, Coming up with To-Dos, Waiting for you.

Presentation only: every number comes from the code that already feeds the Overview, Coming up, To-Dos and the queue
(no new reader, no data-model change)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.models.todo import Todo
from app.services import review
from app.services.coming_up import coming_up
from app.services.overview_service import (_bank_reported_cash, _cash_account_ids, _monthly_flow_totals,
                                           _same_day_flow_totals)
from app.ui.charts import Item

MONTHS_SHOWN = 3
COMING_SHOWN = 6


def _key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _months_back(today: date, n: int) -> list[str]:
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


def _abbr(key: str) -> str:
    return date(int(key[:4]), int(key[5:]), 1).strftime("%b")


@dataclass
class MoneyBlock:
    title: str                       # "Earnings" | "Spending"
    now: float                       # the month so far
    items: list[Item]
    delta_pct: Optional[float]       # vs last month by the same day; None when last month had nothing by then
    last_label: str                  # "Oct"
    good_when_up: bool

    @property
    def delta_good(self) -> Optional[bool]:
        if self.delta_pct is None or abs(self.delta_pct) < 0.05:
            return None
        return (self.delta_pct > 0) == self.good_when_up


@dataclass
class Available:
    balance: float
    as_of: date
    still_due: float
    still_due_count: int

    @property
    def after(self) -> float:
        return round(self.balance - self.still_due, 2)


@dataclass
class ComingLine:
    when: date
    name: str
    amount: Optional[float]
    link: str
    tag: str                         # "Bill" | "Payment" | "To-Do"
    note: str = ""


@dataclass
class WaitingCard:
    label: str
    count: int
    unit: str
    button: str
    link: str


@dataclass
class HomeData:
    today: date
    earnings: MoneyBlock
    spending: MoneyBlock
    available: Optional[Available]
    coming: list[ComingLine] = field(default_factory=list)
    waiting: list[WaitingCard] = field(default_factory=list)

    @property
    def waiting_total(self) -> int:
        """As the menu counts: everything but the unconfirmed merchants, which have their own card."""
        return sum(c.count for c in self.waiting if c.label != "Merchants")


def _block(title: str, full: dict[str, float], by_day: dict[str, float], today: date, good_when_up: bool) -> MoneyBlock:
    keys = _months_back(today, MONTHS_SHOWN)
    current = keys[-1]
    items = [Item(_abbr(k), round(by_day.get(k, 0.0), 2)) if k == current else
             Item(_abbr(k), round(by_day.get(k, 0.0), 2), full=round(full.get(k, 0.0), 2)) for k in keys]
    items[-1] = Item(items[-1].label, items[-1].value, current=True)
    now, last = by_day.get(current, 0.0), by_day.get(keys[-2], 0.0)
    delta = round(100 * (now - last) / last, 1) if last else None
    return MoneyBlock(title, round(now, 2), items, delta, _abbr(keys[-2]), good_when_up)


def money_this_month(session: Session, today: date) -> tuple[MoneyBlock, MoneyBlock]:
    """Earnings = incoming bank lines without the family's own transfers (transfers never count as income or spending
    in the Overview's totals); Spending = outgoing lines, the same way."""
    income, expense = _monthly_flow_totals(session, today)
    income_by, expense_by = _same_day_flow_totals(session, today)
    return (_block("Earnings", income, income_by, today, True),
            _block("Spending", expense, expense_by, today, False))


def available(session: Session, today: date) -> Optional[Available]:
    """The cash balance (the bank's last balance plus every line stored since) minus what is still due this month:
    unpaid bills and expected regular payments from today to the month's end, and anything overdue."""
    reported = _bank_reported_cash(session, _cash_account_ids(session))
    if reported is None:
        return None
    view = coming_up(session, today)
    due = [i for m in view.months if m.label == _key(today) for i in m.items] + view.overdue
    return Available(reported[0], reported[1], round(sum(i.amount for i in due), 2), len(due))


def coming_lines(session: Session, today: date, n: int = COMING_SHOWN) -> list[ComingLine]:
    view = coming_up(session, today)
    lines = [ComingLine(i.when, i.name, i.amount, i.link, "Bill" if i.kind == "bill" else "Payment", i.note)
             for i in view.overdue + [i for m in view.months for i in m.items]]
    todos = session.exec(select(Todo).where(Todo.done == False, Todo.due_date.is_not(None),  # noqa: E712
                                            Todo.transaction_id.is_(None))).all()   # a bill's to-do is its bill above
    lines += [ComingLine(t.due_date, t.title, None, "/todos", "To-Do",
                         "overdue" if t.due_date < today else "") for t in todos]
    lines.sort(key=lambda l: (l.when, l.name))
    return lines[:n]


def waiting_cards(session: Session) -> list[WaitingCard]:
    """Four slim cards: area, count, one button. The unconfirmed merchants are their own card (and their own line in
    the queue), so the other counts are not drowned by them."""
    by_area = review.area_counts(session, merchants=False)
    merchants = review.merchant_count(session)
    return [
        WaitingCard("Inbox", by_area.get("inbox", 0), "papers", "Review", "/waiting?area=inbox"),
        WaitingCard("Money", by_area.get("money", 0), "to check", "Check", "/waiting?area=money"),
        WaitingCard("Health", by_area.get("health", 0), "to confirm", "Confirm", "/waiting?area=health"),
        WaitingCard("Merchants", merchants, "unconfirmed", "Open", f"/waiting?area={review.MERCHANTS}"),
    ]


def home_data(session: Session, today: Optional[date] = None) -> HomeData:
    today = today or date.today()
    review.sync(session)
    earnings, spending = money_this_month(session, today)
    return HomeData(today, earnings, spending, available(session, today), coming_lines(session, today),
                    waiting_cards(session))


__all__ = ["HomeData", "MoneyBlock", "Available", "ComingLine", "WaitingCard", "home_data", "money_this_month",
           "available", "coming_lines", "waiting_cards"]
