"""House tab: /house/*."""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.db import get_session
from app.domains.base import SourceKind
from app.domains.entries import record_for_document
from app.domains.fields import (
    InvalidClassification, build_form_fields, describe_document, load_fields, media_kind_for,
)
from app.domains.house.categories import ITEM_PAGE_TYPE
from app.domains.house.items import build_item_cards, group_item_cards, reference_sections
from app.domains.house.warranty import REMINDER_LEAD_DAYS, house_derived_fields
from app.domains.registry import document_url, get_spec, record_url
from app.models.document import Document, DocumentSource
from app.models.domain import Domain
from app.models.record import Record
from app.services.ingestion import (
    Classification, IncomingFile, RecordInput, RefileRefusedError, attach_file, create_record, ingest,
    update_document_fields, update_record_fields,
)
from app.services.todo_backlog import backlog_context
from app.services.wiki_store import PageRef, find_page, normalize_entity_key
from app.templating import templates

router = APIRouter(prefix="/house", tags=["house"])

_NON_FIELD_KEYS = {"file", "category"}


def _upload_context(session: Session, category: Optional[str], values: dict, errors: dict) -> dict:
    spec = get_spec(Domain.HOUSE)
    known = category if category and any(c.value == category for c in spec.categories) else None
    return {
        "categories": spec.categories,
        "category": known,
        "form_fields": build_form_fields(session, spec, known, values, errors) if known else [],
        "errors": errors,
        "optional_file_labels": [c.label for c in spec.categories if c.kind == SourceKind.RECORD],
    }


def _house_document_or_404(session: Session, document_id: int) -> Document:
    document = session.get(Document, document_id)
    if document is None or document.domain != Domain.HOUSE:
        raise HTTPException(status_code=404, detail="House document not found")
    return document


def _house_record_or_404(session: Session, record_id: int) -> Record:
    record = session.get(Record, record_id)
    if record is None or record.domain != Domain.HOUSE or record.retired_at is not None:
        raise HTTPException(status_code=404, detail="House record not found")
    return record


def _record_context(session: Session, record: Record, values: dict, errors: dict) -> dict:
    spec = get_spec(Domain.HOUSE)
    attachment = session.get(Document, record.document_id) if record.document_id else None
    media_kind = media_kind_for(attachment.filename) if attachment else None
    return {
        "record": record, "category_label": spec.category_label(record.category),
        "attachment": attachment, "attachment_description": describe_document(attachment) if attachment else None,
        "media_kind": media_kind.value if media_kind else None,
        "form_fields": build_form_fields(session, spec, record.category, values, errors, media_kind=media_kind),
        "errors": errors,
    }


def _detail_context(session: Session, document: Document, values: dict, errors: dict) -> dict:
    spec = get_spec(Domain.HOUSE)
    fields = load_fields(document)
    item_page = None
    if fields.get("item_name"):
        item_page = find_page(session, PageRef(ITEM_PAGE_TYPE, fields["item_name"], normalize_entity_key(fields["item_name"])))
    media_kind = media_kind_for(document.filename)
    return {
        "document": document,
        "description": describe_document(document),
        "media_kind": media_kind.value,
        "form_fields": build_form_fields(session, spec, document.category, values, errors, media_kind=media_kind),
        "item_page": item_page,
        "warranty_note": house_derived_fields(document.category, load_fields(document)).get("warranty_expiry_note"),
    }


@router.get("")
async def house_landing(request: Request, group_by: str = "type", session: Session = Depends(get_session)):
    if group_by not in ("type", "room"):
        group_by = "type"
    spec = get_spec(Domain.HOUSE)
    return templates.TemplateResponse(request, "house/landing.html", {
        "group_by": group_by,
        "sections": group_item_cards(build_item_cards(session), group_by),
        "reference": reference_sections(session),
        "category_label": spec.category_label,
        "today": date.today(),
        "reminder_days": REMINDER_LEAD_DAYS,
        **backlog_context(session, Domain.HOUSE),
    })


@router.get("/upload")
async def upload_form(
    request: Request, category: Optional[str] = None, item_name: Optional[str] = None,
    session: Session = Depends(get_session),
):
    values = {"item_name": item_name} if item_name else {}
    return templates.TemplateResponse(request, "house/upload.html", _upload_context(session, category, values, {}))


@router.get("/upload/fields")
async def upload_fields(request: Request, category: Optional[str] = None, session: Session = Depends(get_session)):
    context = _upload_context(session, category, {}, {})
    return templates.TemplateResponse(request, "domains/_field_inputs.html", context)


@router.post("/upload")
async def upload(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    category = (form.get("category") or "").strip() or None
    values = {k: v for k, v in form.items() if k not in _NON_FIELD_KEYS and isinstance(v, str)}
    upload_file = form.get("file")
    spec = get_spec(Domain.HOUSE)
    is_record = category is not None and any(c.value == category and c.kind == SourceKind.RECORD for c in spec.categories)
    has_file = upload_file is not None and not isinstance(upload_file, str) and bool(upload_file.filename)
    if is_record:
        attachment = None
        if has_file:
            attachment = IncomingFile(upload_file.filename, await upload_file.read(), DocumentSource.MANUAL,
                                      getattr(request.state, "user_email", None))
        try:
            record = await create_record(session, RecordInput(Domain.HOUSE, category, values,
                                                              getattr(request.state, "user_email", None), attachment))
        except InvalidClassification as exc:
            return templates.TemplateResponse(request, "house/upload.html",
                                              _upload_context(session, category, values, exc.errors), status_code=400)
        return RedirectResponse(record_url(record), status_code=303)
    if upload_file is None or isinstance(upload_file, str) or not upload_file.filename:
        return templates.TemplateResponse(
            request, "house/upload.html",
            _upload_context(session, category, values, {"file": "Choose a file to upload"}), status_code=400,
        )
    content = await upload_file.read()
    try:
        result = await ingest(
            session,
            IncomingFile(upload_file.filename, content, DocumentSource.MANUAL, getattr(request.state, "user_email", None)),
            Classification(domain=Domain.HOUSE, category=category, fields=values),
        )
    except InvalidClassification as exc:
        return templates.TemplateResponse(
            request, "house/upload.html", _upload_context(session, category, values, exc.errors), status_code=400,
        )
    return RedirectResponse(document_url(result.document) or "/house", status_code=303)


@router.get("/documents/{document_id}")
async def document_detail(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = _house_document_or_404(session, document_id)
    attached = record_for_document(session, document)
    if attached:
        return RedirectResponse(record_url(attached), status_code=303)
    return templates.TemplateResponse(
        request, "house/detail.html", _detail_context(session, document, load_fields(document), {}),
    )


@router.post("/documents/{document_id}/fields")
async def update_fields(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = _house_document_or_404(session, document_id)
    form = await request.form()
    values = {k: v for k, v in form.items() if isinstance(v, str)}
    try:
        await update_document_fields(session, document, values)
    except InvalidClassification as exc:
        return templates.TemplateResponse(
            request, "house/detail.html", _detail_context(session, document, values, exc.errors), status_code=400,
        )
    return RedirectResponse(f"/house/documents/{document.id}", status_code=303)


@router.get("/records/{record_id}")
async def record_detail(request: Request, record_id: int, session: Session = Depends(get_session)):
    record = _house_record_or_404(session, record_id)
    return templates.TemplateResponse(request, "house/record.html", _record_context(session, record, load_fields(record), {}))


@router.post("/records/{record_id}/fields")
async def update_record(request: Request, record_id: int, session: Session = Depends(get_session)):
    record = _house_record_or_404(session, record_id)
    values = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    try:
        await update_record_fields(session, record, values)
    except InvalidClassification as exc:
        return templates.TemplateResponse(request, "house/record.html", _record_context(session, record, values, exc.errors), status_code=400)
    return RedirectResponse(f"/house/records/{record.id}", status_code=303)


@router.post("/records/{record_id}/attachment")
async def attach(request: Request, record_id: int, session: Session = Depends(get_session)):
    record = _house_record_or_404(session, record_id)
    upload_file = (await request.form()).get("file")
    if upload_file is None or isinstance(upload_file, str) or not upload_file.filename:
        return templates.TemplateResponse(request, "house/record.html",
                                          _record_context(session, record, load_fields(record), {"file": "Choose a file"}), status_code=400)
    try:
        await attach_file(session, record, IncomingFile(upload_file.filename, await upload_file.read(),
                                                        DocumentSource.MANUAL, getattr(request.state, "user_email", None)))
    except (InvalidClassification, RefileRefusedError) as exc:
        errors = exc.errors if isinstance(exc, InvalidClassification) else {"file": str(exc)}
        return templates.TemplateResponse(request, "house/record.html",
                                          _record_context(session, record, load_fields(record), errors), status_code=400)
    return RedirectResponse(f"/house/records/{record.id}", status_code=303)
