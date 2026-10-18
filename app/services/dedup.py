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


SAME_BILL_DAYS = 10     # an invoice and its detail sheet are dated within days of each other; the next month's is ~30 apart


def find_duplicate_transaction(
    session: Session, description: str, statement_period: Optional[str],
    amount: Optional[float] = None, issue_date: Optional[date] = None,
) -> Optional[Transaction]:
    """Is this bill one the Hub already holds? Only BILL-derived transactions count (statement line items
    legitimately share provider and period with each other and with unrelated bills).

    Without an amount: the same provider and statement period (the original rule).
    With an amount (Pedro, 2026-10-17: the model's billing period is not reliable, and it writes a provider's name a
    little differently on an invoice and on its detail sheet): the same provider (exact or similar name), the same
    amount, and issue dates within SAME_BILL_DAYS. If either date is unknown, the same statement period is required
    instead. Two consecutive monthly bills of the same amount are ~30 days apart, so they stay two bills."""
    if not statement_period and amount is None:
        return None
    bills = (
        # keeps held rows: bill re-upload duplicate check must see every stored bill row
        select(Transaction)
        .join(Document, Transaction.document_id == Document.id)
        .where(or_(Document.category.is_(None), Document.category != "statement"))
    )
    if amount is None:
        return session.exec(bills.where(Transaction.description == description,
                                        Transaction.statement_period == statement_period)).first()
    for candidate in session.exec(bills.where(Transaction.amount >= amount - 0.005, Transaction.amount <= amount + 0.005)).all():
        if not (candidate.description == description or _similar_provider(description, candidate.description)):
            continue
        if issue_date is not None and candidate.issue_date is not None:
            same_bill = abs((candidate.issue_date - issue_date).days) <= SAME_BILL_DAYS
        else:
            same_bill = bool(statement_period) and candidate.statement_period == statement_period
        if same_bill:
            return candidate
    return None
