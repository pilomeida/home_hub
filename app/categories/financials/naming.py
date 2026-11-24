"""How a Money paper is named from what the Hub already read of it (PLAN step 12e): a bill by its provider and
period, a statement by its account and month, both dated by the paper; a statement's owner is who it is about."""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from app.categories.fields import load_fields
from app.categories.financials.kinds import FinancialsKind
from app.models.account import Account
from app.models.document import Document
from app.models.subject import Subject
from app.models.transaction import Transaction
from app.services.paper_names import short_party


def _owner(session: Session, account: Optional[Account]) -> str:
    subject = session.get(Subject, account.owner_subject_id) if account and account.owner_subject_id else None
    return subject.name if subject is not None else ""


def money_name_parts(session: Session, document: Document) -> dict:
    lines = list(session.exec(select(Transaction).where(Transaction.document_id == document.id)))
    account = session.get(Account, document.account_id) if document.account_id else None
    if account is None and lines and lines[0].account_id:
        account = session.get(Account, lines[0].account_id)
    if document.paper_kind == FinancialsKind.BILL.value:
        if not lines:
            return {}
        bill = lines[0]
        when = bill.issue_date or bill.due_date
        return {"paper_date": when.isoformat() if when else "",
                "what": " ".join(filter(None, [short_party(bill.description), bill.statement_period])), "who": ""}
    issued = load_fields(document).get("issued") or ""
    days = [d for d in (t.paid_date or t.issue_date for t in lines) if d is not None]
    last = issued or (max(days).isoformat() if days else "")
    what = " ".join(filter(None, [short_party(account.name or account.institution), last[:7]])) if account else ""
    return {"paper_date": last, "what": what, "who": _owner(session, account)}
