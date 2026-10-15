"""The "To check" list (decisions D11, D17): statement lines the Hub was unsure about, and statements whose
lines do not add up to their balances, waiting for a person to decide.

`items` / `count` say what is waiting. The four actions (`confirm`, `correct`, `reject`, `accept_difference`)
each change exactly one thing, record who decided and when, and refuse with a three-part message
(`ToCheckError.problem`, see app/problems.py) when the item is not waiting any more or the input is not valid.
A line that is waiting counts nowhere (Transaction.counts()); confirming or correcting it makes it count."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, Union

from sqlmodel import Session, func, select

from app.models.document import Document
from app.models.transaction import StatementCheck, Transaction
from app.problems import Problem

NEEDS_CHECK = "needs_check"
MAX_DESCRIPTION = 200
RELOAD = "Reload the page to see what is still waiting."


class ToCheckError(Exception):
    """A refusal, carrying the three-part message to show. `status` is the HTTP status the page answers with."""

    def __init__(self, problem: Problem, status: int = 409):
        super().__init__(problem.text)
        self.problem = problem
        self.status = status


@dataclass
class Group:
    """Everything waiting in one statement: its doubtful lines (in page, then date order) and, when the
    statement does not add up, the mismatch."""
    document: Document
    lines: list[Transaction] = field(default_factory=list)
    mismatch: Optional[StatementCheck] = None


def _open_mismatches():
    return select(StatementCheck).where(StatementCheck.status == "mismatch", StatementCheck.resolved_at.is_(None))


def items(session: Session) -> list[Group]:
    """The open items, grouped by statement document (oldest document first)."""
    lines = session.exec(
        select(Transaction).where(Transaction.review_status == NEEDS_CHECK)
        .order_by(Transaction.document_id, Transaction.source_page.is_(None), Transaction.source_page,
                  Transaction.paid_date, Transaction.id)).all()
    mismatches = list(session.exec(_open_mismatches()).all())
    groups: dict[int, Group] = {}
    for doc_id in sorted({t.document_id for t in lines} | {c.document_id for c in mismatches}):
        document = session.get(Document, doc_id)
        if document is not None:
            groups[doc_id] = Group(document)
    for t in lines:
        if t.document_id in groups:
            groups[t.document_id].lines.append(t)
    for c in mismatches:
        if c.document_id in groups:
            groups[c.document_id].mismatch = c
    return list(groups.values())


def count(session: Session) -> int:
    held = session.exec(select(func.count()).select_from(Transaction).where(Transaction.review_status == NEEDS_CHECK)).one()
    differing = session.exec(select(func.count()).select_from(StatementCheck).where(
        StatementCheck.status == "mismatch", StatementCheck.resolved_at.is_(None))).one()
    return held + differing


def menu_count() -> int:
    """For the menu. Opens its own session; never raises (a menu must not break a page)."""
    try:
        from app.db import engine
        with Session(engine) as s:
            return count(s)
    except Exception:
        return 0


# --- messages ------------------------------------------------------------------------------------

def _who(by: Optional[str]) -> str:
    return (by or "").strip() or "someone"


def _when(at: Optional[datetime]) -> str:
    return at.strftime("%d %b %Y, %H:%M") if at else "earlier"


_DECIDED = {"confirmed": "confirmed as right", "rejected": "marked as not a real line"}


def _gone(what: str, transaction: Optional[Transaction]) -> ToCheckError:
    if transaction is None:
        return ToCheckError(Problem(what, "That line isn't in the Hub any more; the statement it came from may have been removed.",
                                    RELOAD), status=404)
    state = _DECIDED.get(transaction.review_status or "")
    if state is None:
        return ToCheckError(Problem(what, "This line was never held for checking, so there is nothing to decide about it.", RELOAD))
    return ToCheckError(Problem(what, f"Someone has already decided: it was {state} by {_who(transaction.checked_by)} "
                                      f"({_when(transaction.checked_at)}). A line can be decided only once.",
                                "Nothing was changed. " + RELOAD))


def _waiting_line(session: Session, transaction_id: int, what: str) -> Transaction:
    transaction = session.get(Transaction, transaction_id)
    if transaction is None or transaction.review_status != NEEDS_CHECK:
        raise _gone(what, transaction)
    return transaction


def _stamp(transaction: Transaction, status: str, by: Optional[str]) -> None:
    transaction.review_status = status
    transaction.checked_by = _who(by)
    transaction.checked_at = datetime.utcnow()


# --- actions --------------------------------------------------------------------------------------

def confirm(session: Session, transaction_id: int, by: Optional[str]) -> Transaction:
    transaction = _waiting_line(session, transaction_id, "Not confirmed.")
    _stamp(transaction, "confirmed", by)
    session.add(transaction)
    session.commit()
    session.refresh(transaction)
    return transaction


def reject(session: Session, transaction_id: int, by: Optional[str]) -> Transaction:
    transaction = _waiting_line(session, transaction_id, "Not removed.")
    _stamp(transaction, "rejected", by)
    session.add(transaction)
    session.commit()
    session.refresh(transaction)
    return transaction


def line_date(transaction: Transaction) -> Optional[date]:
    """A statement line's date is stored as its paid date."""
    return transaction.paid_date or transaction.due_date


def _invalid(why: str, next_step: str) -> ToCheckError:
    return ToCheckError(Problem("Not saved.", why, next_step), status=422)


def _parse_date(value: Union[date, str]) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise _invalid(f"“{text}” is not a real date (the Hub needs a day that exists, written year-month-day).",
                       "Pick the date from the date box, or type it like 2026-03-27, then save again.")


def _parse_amount(value: Union[float, int, str]) -> float:
    text = str(value).strip().replace("€", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(",", "") if text.rfind(".") > text.rfind(",") else text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        amount = float(text)
    except ValueError:
        amount = math.nan
    if not math.isfinite(amount) or amount == 0:
        raise _invalid(f"“{str(value).strip()}” is not an amount the Hub can use (it needs to be a number other than zero).",
                       "Type the amount as a number such as 12.50 (the sign comes from whether the line is money in or out), then save again.")
    return round(abs(amount), 2)


def _parse_description(value: str) -> str:
    text = " ".join(str(value).split())
    if not text:
        raise _invalid("The description is empty, and a line needs some words that say what it was.",
                       "Type what the line was for, or use “Not a real line” if it should not be there.")
    if len(text) > MAX_DESCRIPTION:
        raise _invalid(f"The description is longer than {MAX_DESCRIPTION} characters.", "Shorten it and save again.")
    return text


def correct(session: Session, transaction_id: int, by: Optional[str], *, date: Union[date, str, None] = None,  # noqa: A002
            description: Optional[str] = None, amount: Union[float, int, str, None] = None) -> Transaction:
    """Replace the values that were read wrongly. The first correction keeps what was originally read in
    `review_original_json` (later ones never overwrite it). Sets the line to confirmed."""
    transaction = _waiting_line(session, transaction_id, "Not saved.")
    new_date = _parse_date(date) if date not in (None, "") else None
    new_description = _parse_description(description) if description is not None else None
    new_amount = _parse_amount(amount) if amount not in (None, "") else None

    old_date = line_date(transaction)
    changes = {
        "date": new_date if new_date is not None and new_date != old_date else None,
        "description": new_description if new_description is not None and new_description != transaction.description else None,
        "amount": new_amount if new_amount is not None and new_amount != transaction.amount else None,
    }
    if not any(v is not None for v in changes.values()):
        raise _invalid("Nothing was changed: the values you typed are the ones the Hub already has.",
                       "Change at least one value, or press “Looks right” if they are correct as they are.")

    if transaction.review_original_json is None:
        transaction.review_original_json = json.dumps({
            "date": old_date.isoformat() if old_date else None,
            "description": transaction.description,
            "amount": transaction.amount,
        })
    if changes["date"] is not None:
        transaction.paid_date = changes["date"]
    if changes["description"] is not None:
        transaction.description = changes["description"]
    if changes["amount"] is not None:
        transaction.amount = changes["amount"]
    _stamp(transaction, "confirmed", by)
    session.add(transaction)
    session.commit()
    session.refresh(transaction)
    return transaction


def accept_difference(session: Session, document_id: int, by: Optional[str]) -> StatementCheck:
    what = "Not accepted."
    check = session.exec(select(StatementCheck).where(StatementCheck.document_id == document_id)).first()
    if check is None:
        raise ToCheckError(Problem(what, "The Hub has no balance check for that statement any more.", RELOAD), status=404)
    if check.status != "mismatch" or check.resolved_at is not None:
        who = f" by {_who(check.resolved_by)} ({_when(check.resolved_at)})" if check.resolved_at else ""
        why = (f"Someone has already looked at it: the difference was accepted{who}." if check.status == "accepted"
               else "This statement is not flagged as adding up wrongly, so there is no difference to accept.")
        raise ToCheckError(Problem(what, why, "Nothing was changed. " + RELOAD))
    check.status = "accepted"
    check.resolved_by = _who(by)
    check.resolved_at = datetime.utcnow()
    session.add(check)
    session.commit()
    session.refresh(check)
    return check


# --- wording for the cards ---------------------------------------------------------------------------

def money(value: Optional[float], *, signed: bool = False) -> str:
    if value is None:
        return "—"
    sign = "-" if signed and value < 0 else ""
    return f"{sign}€{abs(value):,.2f}"


def mismatch_sentence(check: StatementCheck) -> str:
    """“The lines add up to X but the statement says Y (difference Z)”, from the stored check. X is the sum of
    the lines read, Y what the statement's own balances say they should come to (closing minus opening)."""
    if check.lines_total is None or check.difference is None:
        return check.note or "The lines do not add up to the balances printed on the statement."
    said = check.lines_total + check.difference
    return (f"The lines add up to {money(check.lines_total, signed=True)} but the statement says {money(said, signed=True)} "
            f"(difference {money(check.difference)}).")
