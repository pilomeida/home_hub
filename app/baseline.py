"""The baseline (Pedro, 2026-10-17): the family's books start on 10 Oct 2025, the first day of the bank
export. Older records stay in the database, but they are not shown and not counted anywhere; the opening
position on the baseline day (bank balances, loans, funds) lives in `baseline_positions`.

`Transaction.after_baseline()` is the one definition. `Transaction.counts()` already includes it, so every
total, budget, chart and Ask answer follows; screens that list rows add it explicitly."""
from datetime import date

BASELINE_DATE = date(2025, 10, 10)


def baseline_note(session) -> str:
    """The sentence shown on the pages that would otherwise look incomplete: how far back the stored
    records go, and that the older ones are not shown or considered."""
    from sqlalchemy import func
    from sqlmodel import select

    from app.models.transaction import Transaction

    earliest = session.exec(select(func.min(
        func.coalesce(Transaction.paid_date, Transaction.due_date, Transaction.issue_date)))).one()
    if earliest is None or earliest >= BASELINE_DATE:
        return ""
    return (f"Statement records go back to {_iso(earliest)}, but only records from {_iso(BASELINE_DATE)} "
            f"on are shown and considered. Older ones are kept and not used.")


def _iso(d) -> str:
    return d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]

