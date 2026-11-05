"""Coming up (work-list B3b, Pedro 2026-11-04): what is due in the next three months, for cash flow.

Two kinds of item, each counted once:
- a **commitment**: a known spend that is coming (Netflix monthly, IMI yearly), set up by a person. Its dates roll
  forward from its next date by how often it repeats.
- an **unpaid bill**: a bill already filed whose payment has not shown up: not settled by a bank line, no paid date,
  and its to-do, if it has one, still open. A bill on the same place of the spending tree within 20 days of a
  commitment's date is that payment: the bill is shown, the commitment's date is not.

A budget is something else (Pedro, 2026-11-03): a planned maximum to monitor against, on the Overview. Nothing here
changes a budget or a total."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models.category_node import CategoryNode
from app.models.commitment import Cadence, Commitment
from app.models.debt import Debt
from app.models.document import Document
from app.models.todo import Todo
from app.models.transaction import Transaction
from app.services.taxonomy import path_label

HORIZON_MONTHS = 3
SAME_PAYMENT_DAYS = 20
_STEP_MONTHS = {Cadence.MONTHLY: 1, Cadence.QUARTERLY: 3, Cadence.YEARLY: 12}


def add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class ComingItem:
    when: date
    name: str
    amount: float
    place: Optional[str]          # where it sits on the spending tree
    kind: str                     # "commitment" | "bill"
    link: str
    note: str = ""


@dataclass
class ComingMonth:
    label: str                    # "2026-11"
    items: list[ComingItem] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(i.amount for i in self.items), 2)


@dataclass
class ComingUp:
    today: date
    until: date
    overdue: list[ComingItem]
    months: list[ComingMonth]

    @property
    def total(self) -> float:
        return round(sum(m.total for m in self.months) + sum(i.amount for i in self.overdue), 2)


def occurrences(commitment: Commitment, start: date, end: date) -> list[date]:
    """The commitment's dates from `start` to `end`: its next date, rolled forward by how often it repeats."""
    day = commitment.next_due_date
    if day is None:
        return []
    step = _STEP_MONTHS.get(commitment.cadence)
    if step is None:                                   # irregular: only the date it was given
        return [day] if start <= day <= end else []
    while day < start:
        day = add_months(day, step)
    out = []
    while day <= end:
        out.append(day)
        day = add_months(day, step)
    return out


def _place(session: Session, category_id: Optional[int]) -> Optional[str]:
    node = session.get(CategoryNode, category_id) if category_id else None
    return path_label(session, node) if node is not None else None


def _unpaid_bills(session: Session, until: date) -> list[tuple[Transaction, Document]]:
    rows = session.exec(
        select(Transaction, Document).join(Document, Document.id == Transaction.document_id)
        .where(Document.paper_kind == "bill", Transaction.counts(), Transaction.paid_date.is_(None),
               Transaction.due_date.is_not(None), Transaction.due_date <= until)).all()
    out = []
    for txn, document in rows:
        todo = session.exec(select(Todo).where(Todo.transaction_id == txn.id)).first()
        if todo is None or not todo.done:
            out.append((txn, document))
    return out


def coming_up(session: Session, today: date, months: int = HORIZON_MONTHS) -> ComingUp:
    until = add_months(today, months)
    bills, overdue = [], []
    for txn, document in _unpaid_bills(session, until):
        todo = session.exec(select(Todo).where(Todo.transaction_id == txn.id)).first()
        item = ComingItem(txn.due_date, txn.description, txn.amount, _place(session, txn.category_id), "bill",
                          f"/financials/bills/{document.id}")
        if txn.due_date >= today:
            bills.append((txn, item))
        elif todo is not None:                         # past due and its to-do still open: it is overdue
            overdue.append(ComingItem(item.when, item.name, item.amount, item.place, "bill", item.link,
                                      note="overdue: not paid yet"))

    items = [item for _txn, item in bills]
    for commitment in session.exec(select(Commitment)).all():
        for day in occurrences(commitment, today, until):
            paid_by = next((txn for txn, _item in bills if txn.category_id and txn.category_id == commitment.category_id
                            and abs((txn.due_date - day).days) <= SAME_PAYMENT_DAYS), None)
            if paid_by is not None:                    # the bill is this payment: shown once, as the bill
                continue
            items.append(ComingItem(day, commitment.name, commitment.planned_amount,
                                    _place(session, commitment.category_id), "commitment",
                                    f"/financials/coming-up#commitment-{commitment.id}",
                                    note=commitment.cadence.value))

    by_month: dict[str, ComingMonth] = {}
    for item in sorted(items, key=lambda i: (i.when, i.name)):
        by_month.setdefault(item.when.strftime("%Y-%m"), ComingMonth(item.when.strftime("%Y-%m"))).items.append(item)
    return ComingUp(today, until, sorted(overdue, key=lambda i: i.when), list(by_month.values()))


# --- commitments a person sets up --------------------------------------------------------------------------------

class CommitmentRefused(ValueError):
    def __init__(self, what: str, why: str, next_step: str):
        super().__init__(f"{what} {why} {next_step}")
        self.what, self.why, self.next_step = what, why, next_step


def create_commitment(session: Session, *, name: str, amount: str, cadence: str, next_due: str,
                      category_id: Optional[str]) -> Commitment:
    name = (name or "").strip()
    try:
        planned = round(float((amount or "").replace(",", ".")), 2)
    except ValueError:
        planned = None
    try:
        how_often = Cadence(cadence)
    except ValueError:
        how_often = None
    try:
        day = date.fromisoformat(next_due) if next_due else None
    except ValueError:
        day = None
    node = session.get(CategoryNode, int(category_id)) if category_id and category_id.isdigit() else None
    missing = [label for label, ok in (("a name", bool(name)), ("an amount above zero", planned is not None and planned > 0),
                                       ("how often it repeats", how_often is not None), ("its next date", day is not None),
                                       ("its place on the spending tree", node is not None and node.level == 3))
               if not ok]
    if missing:
        raise CommitmentRefused("Not saved.", f"A commitment needs {', '.join(missing)}.",
                                "Fill those in, then save again.")
    commitment = Commitment(name=name, planned_amount=planned, cadence=how_often, next_due_date=day,
                            category_id=node.id, year=day.year if how_often == Cadence.YEARLY else None)
    session.add(commitment)
    session.commit()
    session.refresh(commitment)
    return commitment


def remove_commitment(session: Session, commitment_id: int) -> None:
    commitment = session.get(Commitment, commitment_id)
    if commitment is None:
        raise CommitmentRefused("Not removed.", "That commitment no longer exists.", "Reload the page.")
    lines = session.exec(select(Transaction.id).where(Transaction.commitment_id == commitment_id)).all()
    debts = session.exec(select(Debt.id).where(Debt.commitment_id == commitment_id)).all()
    if lines or debts:
        uses = ", ".join(p for p in (f"{len(lines)} bank line(s)" if lines else "", f"{len(debts)} loan(s)" if debts else "") if p)
        raise CommitmentRefused("Not removed.", f"{commitment.name} is linked to {uses}, which would lose it.",
                                "Unlink them in Transactions or Loans first, then remove it.")
    session.delete(commitment)
    session.commit()


def prefill_from_line(session: Session, transaction_id: Optional[int]) -> dict:
    """"This repeats" on a bank line: the form, filled in from that line, next date one month on."""
    txn = session.get(Transaction, transaction_id) if transaction_id else None
    if txn is None:
        return {}
    when = txn.paid_date or txn.due_date or txn.issue_date
    return {"name": txn.description, "amount": f"{txn.amount:.2f}", "cadence": Cadence.MONTHLY.value,
            "next_due": add_months(when, 1).isoformat() if when else "", "category_id": str(txn.category_id or "")}


def next_days_summary(session: Session, today: date, days: int = 30) -> tuple[int, float]:
    """For the Overview's teaser: how many items, and how much, in the next `days` days (overdue included)."""
    view = coming_up(session, today)
    soon = [i for m in view.months for i in m.items if i.when <= today + timedelta(days=days)] + view.overdue
    return len(soon), round(sum(i.amount for i in soon), 2)
