"""Statements against the bank feed (PLAN step 4a-b, work-list C5).

A statement uploaded for days the bank feed (or another statement of the same account) already brought must not
count those days twice. Each new statement line is matched to a line of the same account from the feed or another
paper: the same direction and amount, the nearest date within
OVERLAP_DAYS (default taken 2026-11-19: 3), one feed line per statement line. A matched statement line is kept (the
statement still adds up and keeps its pages) but marked as settled by its feed line: shown dimmed with a link to
the bank entry and left out of every total, like a bill the bank paid (`settled_by_id`, docs/ARCHITECTURE.md).
A line a person has checked is never changed. The idea of `reconcileTransactions` in actualbudget/actual (MIT)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Iterable

from sqlmodel import Session, select

from app.models.transaction import Transaction

OVERLAP_DAYS = 3


@dataclass
class OverlapReport:
    overlapped: int = 0
    pairs: list[tuple[int, int]] = field(default_factory=list)      # (statement line, feed line)


def match_statement_to_feed(session: Session, lines: Iterable[Transaction]) -> OverlapReport:
    """Mark each statement line the feed already holds. Does not commit."""
    report = OverlapReport()
    lines = [t for t in lines if t.account_id is not None and t.paid_date is not None and t.external_id is None
             and t.settled_by_id is None and not t.checked_by]
    if not lines:
        return report
    taken = set(session.exec(select(Transaction.settled_by_id).where(Transaction.settled_by_id.is_not(None))))
    for line in sorted(lines, key=lambda t: (t.paid_date, t.id)):
        lo, hi = line.paid_date - timedelta(days=OVERLAP_DAYS), line.paid_date + timedelta(days=OVERLAP_DAYS)
        # The account's lines from the feed AND from its other statements (a 4-year statement over monthly ones):
        # overlap is overlap whoever brought it first. Lines of this same paper, and lines already shown as overlap
        # themselves, are not twins.
        feed = session.exec(select(Transaction).where(
            Transaction.account_id == line.account_id, Transaction.document_id != line.document_id,
            Transaction.settled_by_id.is_(None),
            Transaction.transaction_type == line.transaction_type, Transaction.paid_date >= lo,
            Transaction.paid_date <= hi)).all()
        options = [f for f in feed if f.id not in taken and abs(f.amount - line.amount) < 0.005
                   and f.review_status != "rejected"]
        if not options:
            continue
        twin = min(options, key=lambda f: (abs((f.paid_date - line.paid_date).days), f.id))
        taken.add(twin.id)
        line.settled_by_id = twin.id
        session.add(line)
        report.overlapped += 1
        report.pairs.append((line.id, twin.id))
    return report
