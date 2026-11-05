"""The bank feed's statement (work-list B5, Pedro 2026-11-03: a bank line has no privacy level of its own, it takes
its statement's).

A line from the bank feed (services/bankapi/sync.py) arrives without a paper. So that it still has a statement, and
through it a level, each bank account has one standing feed statement: a Money paper of kind "statement", with no
file, from the source "api", known by its key `bank-sync:<account id>`. Every feed line of that account hangs on
it, and is filed, reached and checked exactly as a line read from a paper statement.

It is not a paper: it has no file to open, and it is never re-filed or deleted (its lines came from the bank)."""

from __future__ import annotations

from sqlmodel import Session, select

from app.categories.financials.kinds import FinancialsKind
from app.models.category import Category
from app.models.document import Document, DocumentSource, DocumentStatus

FEED_KEY_PREFIX = "bank-sync:"


def feed_statement(session: Session, account_id: int, bank_name: str) -> Document:
    """The account's feed statement, made the first time the account's feed brings a line."""
    key = f"{FEED_KEY_PREFIX}{account_id}"
    doc = session.exec(select(Document).where(Document.content_hash == key)).first()
    if doc is None:
        doc = Document(
            filename=f"{bank_name} — automatic bank sync", file_path="", content_hash=key,
            source=DocumentSource.API, status=DocumentStatus.PROCESSED,
            category=Category.FINANCIALS, paper_kind=FinancialsKind.STATEMENT.value, account_id=account_id,
        )
        session.add(doc)
        session.flush()
    return doc


def is_feed_statement(document: Document) -> bool:
    return document.source == DocumentSource.API and (document.content_hash or "").startswith(FEED_KEY_PREFIX)
