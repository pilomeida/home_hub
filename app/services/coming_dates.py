"""Coming up, Dates tab (PLAN step 10f): one list of what has a date ahead (the payments and unpaid bills Coming up
already shows, and the open dated To-Dos, as Home does), with an area chip per row and chips to show one area.

Presentation only: the payments and bills are `coming_up.coming_up`'s, unchanged, and each month's total stays the
money due (a To-Do has no amount). A bill's own To-Do is its bill, so it is not listed twice."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.models.category import Category
from app.models.todo import Todo
from app.services.coming_up import ComingUp, coming_up

MONEY = Category.FINANCIALS.value


@dataclass
class DateRow:
    when: date
    name: str
    amount: Optional[float]
    note: str
    area: str                       # a category value ("financials", "health", …) 
    area_label: str
    link: str
    document_id: Optional[int] = None


@dataclass
class DateMonth:
    label: str
    total: float                    # the money due in the month, as Coming up has always shown it
    rows: list[DateRow] = field(default_factory=list)


@dataclass
class DatesView:
    view: ComingUp
    area: Optional[str]
    chips: list[tuple[str, str, int]]   # (key, label, rows) for the areas present
    overdue: list[DateRow]
    months: list[DateMonth]
    hidden: int = 0                     # rows about nobody in particular, left out while one person is chosen


def _label(category: Optional[Category]) -> str:
    from app.templating import category_label
    return category_label(category) if category else "To-Do"


def dates_view(session: Session, today: date, area: Optional[str] = None,
               documents: Optional[set[int]] = None) -> DatesView:
    """`documents`: the Who chip's person's papers; then only rows about one of them are shown."""
    view = coming_up(session, today)

    def money(i, late: bool) -> DateRow:
        note = ("overdue · " if late else "") + ("bill, not paid yet" if i.kind == "bill" else f"commitment ({i.note})")
        return DateRow(i.when, i.name, i.amount, note + (f" · {i.place}" if i.place else " · Not filed yet"),
                       MONEY, "Money", i.link, i.document_id)

    overdue = [money(i, True) for i in view.overdue]
    months = [DateMonth(m.label, m.total, [money(i, False) for i in m.items]) for m in view.months]
    by_label = {m.label: m for m in months}
    todos = session.exec(select(Todo).where(Todo.done == False, Todo.due_date.is_not(None),  # noqa: E712
                                            Todo.transaction_id.is_(None), Todo.due_date <= view.until,
                                            Todo.category.is_not(None))).all()   # a general To-Do stays on To-Dos only
    for t in todos:
        key = t.category.value
        row = DateRow(t.due_date, t.title, None, "To-Do" + (", overdue" if t.due_date < today else ""), key,
                      _label(t.category), "/todos", t.document_id)
        if t.due_date < today:
            overdue.append(row)
            continue
        label = f"{t.due_date.year:04d}-{t.due_date.month:02d}"
        if label not in by_label:
            by_label[label] = DateMonth(label, 0.0)
            months.append(by_label[label])
        by_label[label].rows.append(row)
    hidden = 0
    if documents is not None:                                # the Who chip: one person's rows only
        mine = lambda r: r.document_id is not None and r.document_id in documents   # noqa: E731
        hidden = sum(1 for r in overdue + [r for m in months for r in m.rows] if not mine(r))
        overdue = [r for r in overdue if mine(r)]
        for m in months:
            m.rows = [r for r in m.rows if mine(r)]
            m.total = round(sum(r.amount or 0.0 for r in m.rows), 2)    # the money due for this person
    every = overdue + [r for m in months for r in m.rows]
    counts: dict[str, list] = {}
    for r in every:
        counts.setdefault(r.area, [r.area_label, 0])[1] += 1
    chips = sorted(((k, v[0], v[1]) for k, v in counts.items()), key=lambda c: (c[0] != MONEY, c[1]))
    area = area if area in counts else None                  # an unknown area shows everything
    keep = (lambda r: r.area == area) if area is not None else (lambda r: True)
    overdue = sorted(filter(keep, overdue), key=lambda r: (r.when, r.name))
    months.sort(key=lambda m: m.label)
    for m in months:
        m.rows = sorted(filter(keep, m.rows), key=lambda r: (r.when, r.name))
    return DatesView(view, area, chips, overdue, [m for m in months if m.rows], hidden)


__all__ = ["DateRow", "DateMonth", "DatesView", "dates_view"]
