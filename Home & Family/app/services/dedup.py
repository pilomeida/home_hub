"""Duplicate detection for ingested documents and transactions."""

from typing import Optional

from sqlmodel import Session, or_, select

from app.models.document import Document
from app.models.transaction import Transaction


def find_existing_document_by_hash(session: Session, content_hash: str) -> Optional[Document]:
    statement = select(Document).where(Document.content_hash == content_hash)
    return session.exec(statement).first()


def find_duplicate_transaction(
    session: Session, provider: str, statement_period: Optional[str]
) -> Optional[Transaction]:
    """A transaction is a duplicate if the same provider already has a
    BILL-derived transaction for the same statement period. Statement line
    items (which legitimately share provider/period with each other, and
    can coincidentally share a provider/period with an unrelated bill) are
    excluded — this only applies to true bill re-uploads."""
    if not statement_period:
        return None
    statement = (
        select(Transaction)
        .join(Document, Transaction.document_id == Document.id)
        .where(
            Transaction.provider == provider,
            Transaction.statement_period == statement_period,
            or_(Document.doc_type.is_(None), Document.doc_type != "statement"),
        )
    )
    return session.exec(statement).first()
