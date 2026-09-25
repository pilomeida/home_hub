"""The generic ingestion core -- the single path by which any file, from any
channel (manual upload, backfill script, and Plan B's Email/Telegram),
becomes a Document in a domain.

Two steps, deliberately separable:
  1. receive_file      -- dedup by content hash, store the file, create an
                          UNCLASSIFIED Document (domain/category unset).
  2. finalize_document -- validate a Classification (domain + category +
                          fields) against the domain registry, tag the
                          Document, and hand it to the domain's handler.
A channel that needs human review (Plan B) calls step 1 with its own
initial_status, holds the Document, and calls step 2 on approval. Manual
uploads call `ingest`, which does both. Invariant: Document.domain is set
only here, so "domain IS NOT NULL" means "finalized"."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from sqlmodel import Session

from app.domains.base import DomainSpec, UnknownCategoryError
from app.domains.fields import (
    InvalidClassification, dump_fields, load_fields, media_kind_for, validate_fields,
)
from app.domains.registry import UnknownDomainError, get_spec
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.services.dedup import find_existing_document_by_hash
from app.services.storage import content_hash, save_file


@dataclass(frozen=True)
class IncomingFile:
    filename: str
    content: bytes
    source: DocumentSource
    uploaded_by: Optional[str] = None


@dataclass(frozen=True)
class Classification:
    domain: Domain
    category: Optional[str] = None
    fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ReceiveResult:
    document: Document
    duplicate: bool


@dataclass
class ValidatedClassification:
    spec: DomainSpec
    category: Optional[str]
    fields: dict[str, str]


class AlreadyFinalizedError(Exception):
    pass


class NotFinalizedError(Exception):
    pass


def mark_needs_attention(session: Session, document: Document, reason: str) -> Document:
    document.status = DocumentStatus.NEEDS_ATTENTION
    document.failure_reason = reason
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def receive_file(
    session: Session, incoming: IncomingFile, initial_status: DocumentStatus = DocumentStatus.PENDING
) -> ReceiveResult:
    digest = content_hash(incoming.content)
    existing = find_existing_document_by_hash(session, digest)
    if existing is not None:
        return ReceiveResult(document=existing, duplicate=True)
    document = Document(
        filename=incoming.filename,
        file_path=save_file(incoming.filename, incoming.content),
        content_hash=digest,
        source=incoming.source,
        status=initial_status,
        uploaded_by=incoming.uploaded_by,
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    return ReceiveResult(document=document, duplicate=False)


def validate_classification(
    session: Session, classification: Classification, filename: str
) -> ValidatedClassification:
    try:
        spec = get_spec(classification.domain)
    except UnknownDomainError:
        raise InvalidClassification({"domain": "Choose a domain that exists"}) from None

    category = (classification.category or "").strip() or None
    media_kind = media_kind_for(filename)
    errors: dict[str, str] = {}
    if category is None:
        if not spec.infers_category:
            errors["category"] = "Choose a category"
    else:
        try:
            category_spec = spec.category(category)
        except UnknownCategoryError:
            errors["category"] = f"Unknown {spec.label} category"
        else:
            if media_kind not in category_spec.accepted_media:
                accepted = ", ".join(sorted(m.value for m in category_spec.accepted_media))
                errors["file"] = f"{category_spec.label} accepts {accepted} files only"
    if errors:
        raise InvalidClassification(errors)

    fields = validate_fields(session, spec, category, media_kind, classification.fields)
    return ValidatedClassification(spec=spec, category=category, fields=fields)


async def finalize_document(
    session: Session, document: Document, classification: Classification
) -> Document:
    if document.status == DocumentStatus.PROCESSED:
        raise AlreadyFinalizedError(f"Document {document.id} is already processed")
    validated = validate_classification(session, classification, document.filename)

    document.domain = validated.spec.domain
    document.category = validated.category
    document.fields_json = dump_fields(validated.fields)
    document.status = DocumentStatus.PENDING
    document.failure_reason = None
    session.add(document)
    session.commit()
    session.refresh(document)

    try:
        return await validated.spec.handler.process(session, document)
    except Exception as exc:
        # Broad by design: this is the ingestion boundary. A handler bug or
        # an unanticipated API failure must land the Document on
        # needs_attention with a reason, never leave it stuck at PENDING.
        session.rollback()
        session.refresh(document)
        return mark_needs_attention(session, document, f"{validated.spec.label} processing failed: {exc}")


async def update_document_fields(
    session: Session, document: Document, raw_fields: Mapping[str, Any]
) -> Document:
    if document.domain is None:
        raise NotFinalizedError(f"Document {document.id} has not been finalized")
    spec = get_spec(document.domain)
    previous = load_fields(document)
    fields = validate_fields(session, spec, document.category, media_kind_for(document.filename), raw_fields)
    document.fields_json = dump_fields(fields)
    session.add(document)
    session.commit()
    session.refresh(document)
    await spec.handler.on_fields_changed(session, document, previous)
    session.refresh(document)
    return document


async def ingest(
    session: Session, incoming: IncomingFile, classification: Classification
) -> ReceiveResult:
    validate_classification(session, classification, incoming.filename)
    received = receive_file(session, incoming)
    if received.duplicate:
        return received
    document = await finalize_document(session, received.document, classification)
    return ReceiveResult(document=document, duplicate=False)