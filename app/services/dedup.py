"""Duplicate detection for ingested documents and transactions."""

from typing import Optional

from sqlmodel import Session, select

from app.models.document import Document
from app.models.transaction import Transaction


def find_existing_document_by_hash(session: Session, content_hash: str) -> Optional[Document]:
    statement = select(Document).where(Document.content_hash == content_hash)
    return session.exec(statement).first()


def find_duplicate_transaction(
    session: Session, provider: str, statement_period: Optional[str]
) -> Optional[Transaction]:
    """A transaction is a duplicate if the same provider already has a
    transaction for the same statement period."""
    if not statement_period:
        return None
    statement = select(Transaction).where(
        Transaction.provider == provider,
        Transaction.statement_period == statement_period,
    )
    return session.exec(statement).first()
