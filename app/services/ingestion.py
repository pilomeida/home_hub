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
from datetime import datetime
from typing import Any, Mapping, Optional

from sqlmodel import Session, select

from app.categories.base import CategorySpec, SourceKind, UnknownKindError
from app.categories.entries import record_for_document
from app.categories.fields import (
    InvalidClassification, dump_fields, load_fields, media_kind_for, validate_fields,
)
from app.categories.registry import UnknownCategoryError, get_spec
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.category import Category
from app.models.record import Record
from app.models.todo import Todo
from app.services.dedup import find_existing_document_by_hash
from app.services.storage import content_hash, save_file
from app.privacy.scope import privacy_scope, require_ready
from app.privacy.settings import configured_tier
from app.services.wiki_engine import withdraw_from_wiki


@dataclass(frozen=True)
class IncomingFile:
    filename: str
    content: bytes
    source: DocumentSource
    uploaded_by: Optional[str] = None


@dataclass(frozen=True)
class Classification:
    category: Category
    paper_kind: Optional[str] = None
    fields: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ReceiveResult:
    document: Document
    duplicate: bool


@dataclass
class ValidatedClassification:
    spec: CategorySpec
    paper_kind: Optional[str]
    fields: dict[str, str]


class AlreadyFinalizedError(Exception):
    pass


class NotFinalizedError(Exception):
    pass


class RefileRefusedError(Exception):
    pass


@dataclass(frozen=True)
class RecordInput:
    category: Category
    paper_kind: str
    fields: Mapping[str, Any] = field(default_factory=dict)
    entered_by: Optional[str] = None
    attachment: Optional[IncomingFile] = None


def _category_kind(validated: ValidatedClassification) -> SourceKind:
    if validated.paper_kind is None:
        return SourceKind.DOCUMENT  # only infers_kind domains allow this, and they file documents
    return validated.spec.kind(validated.paper_kind).kind


def _target_kind(classification: Classification) -> Optional[SourceKind]:
    """The target category's kind, read straight off the registry -- before
    validate_classification runs. Lets create_record/refile_record raise
    their own, more specific error instead of validate_classification's
    generic "choose a file" for a DOCUMENT-kind category with no file.
    Returns None when the domain or category is unknown or missing; those
    cases are left for validate_classification to report as usual."""
    try:
        spec = get_spec(classification.category)
    except UnknownCategoryError:
        return None
    paper_kind = (classification.paper_kind or "").strip() or None
    if paper_kind is None:
        return None
    try:
        return spec.kind(paper_kind).kind
    except UnknownKindError:
        return None


def _not_hand_entered_error(spec: CategorySpec, paper_kind: Optional[str]) -> InvalidClassification:
    label = spec.kind_label(paper_kind)
    return InvalidClassification({"category": f"{label} is filed from a document, not entered by hand"})


NO_KIND_REASON = "the Hub couldn't work out what kind of paper it is, so it wasn't filed"


def mark_needs_attention(session: Session, document: Document, reason: str) -> Document:
    document.status = DocumentStatus.NEEDS_ATTENTION
    document.failure_reason = reason
    session.add(document)
    session.commit()
    session.refresh(document)
    # Say what didn't happen, why, and what to do - and keep it findable on the Problems page.
    from app.problems import Problem, explain, record_problem
    try:
        link = None
        if document.category is not None:
            from app.categories.registry import document_url
            link = document_url(document)
        problem = explain(f"{document.filename} wasn't processed.", reason)
        record_problem(session, area="Reading documents",
                       problem=Problem(problem.what, problem.why, problem.next_step, link),
                       ref=f"document:{document.id}", technical=reason)
    except Exception:
        pass   # reporting a problem must never create another one
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
    session: Session, classification: Classification, filename: Optional[str]
) -> ValidatedClassification:
    try:
        spec = get_spec(classification.category)
    except UnknownCategoryError:
        raise InvalidClassification({"domain": "Choose a category that exists"}) from None

    paper_kind = (classification.paper_kind or "").strip() or None
    media_kind = media_kind_for(filename) if filename else None
    errors: dict[str, str] = {}
    if paper_kind is None:
        if not spec.infers_kind:
            errors["category"] = "Choose a kind of paper"
        elif media_kind is None:
            errors["file"] = "Choose a file to upload"
    else:
        try:
            kind_spec = spec.kind(paper_kind)
        except UnknownKindError:
            errors["category"] = f"Unknown {spec.label} kind of paper"
        else:
            if media_kind is None and kind_spec.kind == SourceKind.DOCUMENT:
                errors["file"] = "Choose a file to upload"
            elif media_kind is not None and media_kind not in kind_spec.accepted_media:
                accepted = ", ".join(sorted(m.value for m in kind_spec.accepted_media))
                errors["file"] = f"{kind_spec.label} accepts {accepted} files only"
    if errors:
        raise InvalidClassification(errors)

    fields = validate_fields(session, spec, paper_kind, media_kind, classification.fields)
    return ValidatedClassification(spec=spec, paper_kind=paper_kind, fields=fields)


async def _file_under(session: Session, document: Document, validated: ValidatedClassification) -> Document:
    # Everything below talks to AI on this document's behalf, so it runs inside the
    # document type's privacy scope: its level rides on every call, and a type with
    # no level is held (decision D12). A no-op while levels are not binding.
    with privacy_scope(session, validated.spec.category, validated.paper_kind):
        result = await _file_under_scoped(session, document, validated)
    if result.status == DocumentStatus.PROCESSED and (result.category is None or result.paper_kind is None):
        # Every filed paper has a kind (work-list B6): one the Hub could not give a kind to goes to a person.
        result = mark_needs_attention(session, result, NO_KIND_REASON)
    if result.status == DocumentStatus.PROCESSED:
        from app.problems import resolve_for
        resolve_for(session, f"document:{result.id}")      # it works now: clear its earlier problem
    return result


async def _file_under_scoped(session: Session, document: Document, validated: ValidatedClassification) -> Document:
    document.category = validated.spec.category
    document.paper_kind = validated.paper_kind
    document.sensitivity_tier = configured_tier(session, validated.spec.category, validated.paper_kind)
    document.failure_reason = None
    if _category_kind(validated) == SourceKind.RECORD:
        document.fields_json = "{}"
        document.status = DocumentStatus.PROCESSED
        record = Record(category=validated.spec.category, paper_kind=validated.paper_kind,
                        fields_json=dump_fields(validated.fields), document_id=document.id,
                        entered_by=document.uploaded_by)
        record.sensitivity_tier = document.sensitivity_tier
        session.add(document)
        session.add(record)
        session.commit()
        session.refresh(record)
        try:
            await validated.spec.handler.process_record(session, record)
        except Exception as exc:
            session.rollback()
            session.refresh(document)
            return mark_needs_attention(session, document, f"{validated.spec.label} processing failed: {exc}")
        session.refresh(document)
        return document

    document.fields_json = dump_fields(validated.fields)
    document.status = DocumentStatus.PENDING
    session.add(document)
    session.commit()
    session.refresh(document)
    try:
        return await validated.spec.handler.process(session, document)
    except Exception as exc:
        # Broad by design: the ingestion boundary never leaves a document stuck.
        session.rollback()
        session.refresh(document)
        return mark_needs_attention(session, document, f"{validated.spec.label} processing failed: {exc}")


async def finalize_document(session: Session, document: Document, classification: Classification) -> Document:
    if document.status == DocumentStatus.PROCESSED:
        raise AlreadyFinalizedError(f"Document {document.id} is already processed")
    validated = validate_classification(session, classification, document.filename)
    return await _file_under(session, document, validated)


async def update_document_fields(
    session: Session, document: Document, raw_fields: Mapping[str, Any]
) -> Document:
    if document.category is None:
        raise NotFinalizedError(f"Document {document.id} has not been finalized")
    if record_for_document(session, document) is not None:
        raise RefileRefusedError("This file is attached to a hand-entered record; edit the record instead.")
    spec = get_spec(document.category)
    previous = load_fields(document)
    fields = validate_fields(session, spec, document.paper_kind, media_kind_for(document.filename), raw_fields)
    document.fields_json = dump_fields(fields)
    session.add(document)
    session.commit()
    session.refresh(document)
    with privacy_scope(session, document.category, document.paper_kind):
        await spec.handler.on_fields_changed(session, document, previous)
    session.refresh(document)
    return document


async def create_record(session: Session, record_input: RecordInput) -> Record:
    classification = Classification(record_input.category, record_input.paper_kind, record_input.fields)
    attachment = record_input.attachment
    if _target_kind(classification) == SourceKind.DOCUMENT:
        raise _not_hand_entered_error(get_spec(record_input.category), record_input.paper_kind)
    validated = validate_classification(session, classification, attachment.filename if attachment else None)
    if _category_kind(validated) != SourceKind.RECORD:  # safety net: should already be caught above
        raise _not_hand_entered_error(validated.spec, validated.paper_kind)
    if attachment is not None:
        received = receive_file(session, attachment)
        if received.duplicate:
            existing = record_for_document(session, received.document)
            if existing is not None:
                return existing
            if received.document.category is not None:
                raise InvalidClassification({"file": "This file is already filed elsewhere"})
        document = await _file_under(session, received.document, validated)
        return record_for_document(session, document)
    record = Record(category=validated.spec.category, paper_kind=validated.paper_kind,
                    fields_json=dump_fields(validated.fields), entered_by=record_input.entered_by)
    session.add(record)
    session.commit()
    session.refresh(record)
    with privacy_scope(session, record.category, record.paper_kind):
        await validated.spec.handler.process_record(session, record)
    return record


async def attach_file(session: Session, record: Record, incoming: IncomingFile) -> Record:
    if record.document_id is not None:
        raise RefileRefusedError("This record already has a file attached.")
    spec = get_spec(record.category)
    category_spec = spec.kind(record.paper_kind)
    if media_kind_for(incoming.filename) not in category_spec.accepted_media:
        raise InvalidClassification({"file": f"{category_spec.label} does not accept this file type"})
    received = receive_file(session, incoming)
    document = received.document
    if received.duplicate and document.category is not None:
        raise InvalidClassification({"file": "This file is already filed elsewhere"})
    document.category, document.paper_kind = record.category, record.paper_kind
    document.fields_json, document.status = "{}", DocumentStatus.PROCESSED
    record.document_id = document.id
    record.updated_at = datetime.utcnow()
    session.add(document)
    session.add(record)
    session.commit()
    session.refresh(record)
    with privacy_scope(session, record.category, record.paper_kind):
        await spec.handler.on_record_changed(session, record, load_fields(record))
    return record


async def update_record_fields(session: Session, record: Record, raw_fields: Mapping[str, Any]) -> Record:
    spec = get_spec(record.category)
    attachment = session.get(Document, record.document_id) if record.document_id else None
    previous = load_fields(record)
    fields = validate_fields(session, spec, record.paper_kind,
                             media_kind_for(attachment.filename) if attachment else None, raw_fields)
    record.fields_json = dump_fields(fields)
    record.updated_at = datetime.utcnow()
    session.add(record)
    session.commit()
    session.refresh(record)
    with privacy_scope(session, record.category, record.paper_kind):
        await spec.handler.on_record_changed(session, record, previous)
    session.refresh(record)
    return record


def _drop_open_derived_todos(session: Session, *, document_id: Optional[int] = None, record_id: Optional[int] = None) -> None:
    # To-dos linked to a source were generated from it (manual to-dos never
    # carry a source link): open ones are re-derived by the new handler; done
    # ones are history and stay.
    link = Todo.record_id == record_id if record_id is not None else Todo.document_id == document_id
    for todo in session.exec(select(Todo).where(link, Todo.done == False)).all():  # noqa: E712
        session.delete(todo)
    session.commit()


def _describe(spec: CategorySpec, paper_kind: Optional[str]) -> str:
    return f"{spec.label} / {spec.kind_label(paper_kind) or 'auto'}"


async def refile_document(session: Session, document: Document, classification: Classification) -> Document:
    if document.category is None:
        raise NotFinalizedError(f"Document {document.id} has not been finalized")
    if record_for_document(session, document) is not None:
        raise RefileRefusedError("This file is attached to a hand-entered record; re-file the record instead.")
    validated = validate_classification(session, classification, document.filename)
    if validated.spec.category == document.category and validated.paper_kind == document.paper_kind:
        return await update_document_fields(session, document, classification.fields)

    old_spec = get_spec(document.category)
    blocker = old_spec.handler.refile_blocker(session, document)
    if blocker:
        raise RefileRefusedError(blocker)
    await old_spec.handler.withdraw(session, document)
    _drop_open_derived_todos(session, document_id=document.id)
    await withdraw_from_wiki(session, document, description=(
        f"{document.filename} re-filed: {_describe(old_spec, document.paper_kind)} → "
        f"{_describe(validated.spec, validated.paper_kind)}"
    ))
    return await _file_under(session, document, validated)


async def refile_record(session: Session, record: Record, classification: Classification) -> Record | Document:
    if record.retired_at is not None:
        raise RefileRefusedError("This record was already re-filed.")
    attachment = session.get(Document, record.document_id) if record.document_id else None
    if _target_kind(classification) == SourceKind.DOCUMENT and attachment is None:
        raise RefileRefusedError("A hand-entered record can only become a document if it has a file attached.")
    validated = validate_classification(session, classification, attachment.filename if attachment else None)
    if validated.spec.category == record.category and validated.paper_kind == record.paper_kind:
        return await update_record_fields(session, record, classification.fields)
    target_kind = _category_kind(validated)
    if target_kind == SourceKind.DOCUMENT and attachment is None:
        raise RefileRefusedError("A hand-entered record can only become a document if it has a file attached.")

    old_spec = get_spec(record.category)
    description = f"{_describe(old_spec, record.paper_kind)} record #{record.id} re-filed → {_describe(validated.spec, validated.paper_kind)}"
    _drop_open_derived_todos(session, record_id=record.id)
    await withdraw_from_wiki(session, record, description=description)

    if target_kind == SourceKind.RECORD:
        record.category, record.paper_kind = validated.spec.category, validated.paper_kind
        record.fields_json = dump_fields(validated.fields)
        record.updated_at = datetime.utcnow()
        if attachment is not None:
            attachment.category, attachment.paper_kind = record.category, record.paper_kind
            session.add(attachment)
        session.add(record)
        session.commit()
        session.refresh(record)
        await validated.spec.handler.process_record(session, record)
        return record

    record.retired_at = datetime.utcnow()
    session.add(record)
    session.commit()
    return await _file_under(session, attachment, validated)


def file_for_later(session: Session, incoming: IncomingFile, classification: Classification) -> ReceiveResult:
    """Store the file and file it under its domain at once (status PENDING), without reading it: the caller
    then runs finalize_document in the background, so a slow reading never holds up the person's page."""
    validated = validate_classification(session, classification, incoming.filename)
    require_ready(session, classification.category, validated.paper_kind)   # held types are not even stored
    received = receive_file(session, incoming)
    if received.duplicate:
        return received
    document = received.document
    document.category = validated.spec.category
    document.paper_kind = validated.paper_kind
    document.sensitivity_tier = configured_tier(session, validated.spec.category, validated.paper_kind)
    document.fields_json = dump_fields(validated.fields)
    session.add(document)
    session.commit()
    session.refresh(document)
    return ReceiveResult(document=document, duplicate=False)


async def ingest(
    session: Session, incoming: IncomingFile, classification: Classification
) -> ReceiveResult:
    validated = validate_classification(session, classification, incoming.filename)
    require_ready(session, classification.category, validated.paper_kind)   # held types are not even stored
    received = receive_file(session, incoming)
    if received.duplicate:
        return received
    document = await finalize_document(session, received.document, classification)
    return ReceiveResult(document=document, duplicate=False)