"""The shared Inbox: every document arriving through an ingestion channel
(email, Telegram, any future one) enters via receive_document() and waits,
unfinalized (domain = NULL, status PENDING_REVIEW), until a human approves
or discards it. This is first-time filing only; corrections after filing
belong to Plan A's document edit flow.

Approval is the ONLY place a channel document is finalized: it validates
the human's choice with the ingestion core's validate_classification() and
then calls finalize_document() -- the same core a manual upload uses, which
runs the domain handler and the wiki Ingest. Nothing here touches the wiki
(other than the REVIEW log line), extraction or todos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional

from sqlmodel import Session, select

from app.domains.fields import InvalidClassification
from app.domains.registry import get_spec, is_implemented
from app.models.document import Document, DocumentStatus
from app.models.domain import Domain
from app.models.inbox_item import InboxItem
from app.models.wiki import WikiOperation
from app.services.domain_classifier import (
    CONFIDENT_THRESHOLD,
    DomainSuggestion,
    suggest_domain_and_category,
)
from app.services.ingestion import (
    Classification,
    IncomingFile,
    finalize_document,
    receive_file,
    validate_classification,
)
from app.services.wiki_store import append_log


class InboxError(Exception):
    """The document isn't waiting in the Inbox (unknown, or already handled)."""


@dataclass
class InboxReceipt:
    document: Document
    inbox_item: Optional[InboxItem]
    duplicate: bool


@dataclass
class InboxEntry:
    item: InboxItem
    document: Document
    confident: bool
    suggestion_label: Optional[str]


def item_for(session: Session, document_id: int) -> Optional[InboxItem]:
    return session.exec(select(InboxItem).where(InboxItem.document_id == document_id)).first()


async def receive_document(
    session: Session,
    incoming: IncomingFile,
    *,
    context_text: Optional[str],
    external_ref: Optional[str],
    classify=suggest_domain_and_category,
) -> InboxReceipt:
    received = receive_file(session, incoming, initial_status=DocumentStatus.PENDING_REVIEW)
    document = received.document
    if received.duplicate:
        return InboxReceipt(document=document, inbox_item=item_for(session, document.id), duplicate=True)

    try:
        suggestion = await classify(document.file_path, context_text)
    except Exception as exc:
        # Broad by design: a classifier failure (API down, credits, bad
        # response) must never lose the document -- it arrives without a
        # suggestion and a human chooses.
        suggestion = DomainSuggestion(None, None, 0.0, f"Automatic sorting failed: {exc}")

    item = InboxItem(
        document_id=document.id, context_text=context_text, external_ref=external_ref,
        suggested_domain=suggestion.domain.value if suggestion.domain is not None else None,
        suggested_category=suggestion.category,
        confidence=suggestion.confidence, classifier_note=suggestion.note,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return InboxReceipt(document=document, inbox_item=item, duplicate=False)


def suggested_domain(item: InboxItem) -> Optional[Domain]:
    """The suggestion as a registered Domain, or None if it no longer is one."""
    try:
        domain = Domain(item.suggested_domain) if item.suggested_domain else None
    except ValueError:
        return None
    return domain if is_implemented(domain) else None


def describe(domain: Optional[Domain], category: Optional[str]) -> Optional[str]:
    if not is_implemented(domain):
        return None
    spec = get_spec(domain)
    return f"{spec.label} › {spec.category_label(category)}" if category else spec.label


def is_confident(item: InboxItem) -> bool:
    domain = suggested_domain(item)
    if domain is None or (item.confidence or 0.0) < CONFIDENT_THRESHOLD:
        return False
    spec = get_spec(domain)
    if item.suggested_category is None:
        return spec.infers_category
    return item.suggested_category in {c.value for c in spec.categories}


def _entry(item: InboxItem, document: Document) -> InboxEntry:
    return InboxEntry(
        item=item, document=document, confident=is_confident(item),
        suggestion_label=describe(suggested_domain(item), item.suggested_category),
    )


def entry_for(session: Session, document_id: int) -> Optional[InboxEntry]:
    document = session.get(Document, document_id)
    item = item_for(session, document_id) if document else None
    return _entry(item, document) if item is not None else None


def pending_entries(session: Session) -> list[InboxEntry]:
    rows = session.exec(
        select(InboxItem, Document)
        .join(Document, InboxItem.document_id == Document.id)
        .where(Document.status == DocumentStatus.PENDING_REVIEW)
        .order_by(InboxItem.received_at, InboxItem.id)
    ).all()
    return [_entry(item, document) for item, document in rows]


def recently_reviewed(session: Session, limit: int = 20) -> list[tuple[InboxItem, Document]]:
    return list(session.exec(
        select(InboxItem, Document)
        .join(Document, InboxItem.document_id == Document.id)
        .where(InboxItem.reviewed_at.is_not(None))
        .order_by(InboxItem.reviewed_at.desc())
        .limit(limit)
    ).all())


def _pending(session: Session, document_id: int) -> tuple[Document, InboxItem]:
    document = session.get(Document, document_id)
    item = item_for(session, document_id) if document else None
    if document is None or item is None:
        raise InboxError("That document isn't in the Inbox.")
    if document.status != DocumentStatus.PENDING_REVIEW:
        raise InboxError("That document has already been handled.")
    return document, item


async def approve(
    session: Session,
    document_id: int,
    *,
    domain_value: str,
    category: Optional[str],
    fields: Mapping[str, str],
    reviewed_by: Optional[str],
) -> Document:
    document, item = _pending(session, document_id)
    try:
        domain = Domain(domain_value)
    except ValueError:
        raise InvalidClassification({"domain": "Choose an area"}) from None
    classification = Classification(domain=domain, category=category or None, fields=dict(fields))
    # Validate first so a rejected choice changes nothing (no review mark, no log).
    validate_classification(session, classification, document.filename)

    item.reviewed_by = reviewed_by
    item.reviewed_at = datetime.utcnow()
    session.add(item)
    append_log(
        session, WikiOperation.REVIEW,
        f"Approved {document.filename} as {describe(domain, classification.category)} "
        f"(via {document.source.value}, by {reviewed_by or 'unknown'})",
        document_id=document.id, commit=False,
    )
    session.commit()

    return await finalize_document(session, document, classification)


def discard(session: Session, document_id: int, *, reviewed_by: Optional[str]) -> Document:
    document, item = _pending(session, document_id)
    document.status = DocumentStatus.DISCARDED
    item.reviewed_by = reviewed_by
    item.reviewed_at = datetime.utcnow()
    session.add(document)
    session.add(item)
    append_log(
        session, WikiOperation.REVIEW,
        f"Discarded {document.filename} from the Inbox "
        f"(via {document.source.value}, by {reviewed_by or 'unknown'}); file kept",
        document_id=document.id, commit=False,
    )
    session.commit()
    session.refresh(document)
    return document
