"""Duplicate detection for ingested documents and transactions."""

import re
import unicodedata
from datetime import date
from typing import Optional

from sqlmodel import Session, or_, select

from app.models.document import Document
from app.models.transaction import Transaction


def find_existing_document_by_hash(session: Session, content_hash: str) -> Optional[Document]:
    statement = select(Document).where(Document.content_hash == content_hash)
    return session.exec(statement).first()


def _provider_words(name: str) -> set[str]:
    folded = "".join(c for c in unicodedata.normalize("NFKD", name or "") if not unicodedata.combining(c)).lower()
    return set(re.findall(r"[a-z0-9]+", folded))


def _similar_provider(a: str, b: str) -> bool:
    """The same provider written two ways ("amigo (Vodafone)" / "amigo (Vodafone Portugal)"): every word of the
    shorter name is in the longer one."""
    wa, wb = _provider_words(a), _provider_words(b)
    return bool(wa) and bool(wb) and (wa <= wb or wb <= wa)


def find_duplicate_transaction(
    session: Session, description: str, statement_period: Optional[str],
    amount: Optional[float] = None, issue_date: Optional[date] = None,
) -> Optional[Transaction]:
    """A transaction is a duplicate if the same provider already has a
    BILL-derived transaction for the same statement period. Statement line
    items (which legitimately share provider/period with each other, and
    can coincidentally share a provider/period with an unrelated bill) are
    excluded — this only applies to true bill re-uploads.

    The model writes a provider's name a little differently from one paper to the next (an invoice and its
    detail sheet), so when the amount is known a bill also matches on the same period, the same amount and
    issue date, and a similar provider name."""
    if not statement_period:
        return None
    bills = (
        # keeps held rows: bill re-upload duplicate check must see every stored bill row
        select(Transaction)
        .join(Document, Transaction.document_id == Document.id)
        .where(
            Transaction.statement_period == statement_period,
            or_(Document.category.is_(None), Document.category != "statement"),
        )
    )
    exact = session.exec(bills.where(Transaction.description == description)).first()
    if exact is not None or amount is None:
        return exact
    for candidate in session.exec(bills.where(Transaction.amount >= amount - 0.005, Transaction.amount <= amount + 0.005)).all():
        same_day = issue_date is None or candidate.issue_date is None or candidate.issue_date == issue_date
        if same_day and _similar_provider(description, candidate.description):
            return candidate
    return None
