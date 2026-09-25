"""House tab: /house/*."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.db import get_session
from app.domains.fields import (
    InvalidClassification, build_form_fields, describe_document, load_fields, media_kind_for,
)
from app.domains.house.categories import ITEM_PAGE_TYPE
from app.domains.registry import document_url, get_spec
from app.models.document import Document, DocumentSource
from app.models.domain import Domain
from app.services.ingestion import Classification, IncomingFile, ingest, update_document_fields
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
    }


def _house_document_or_404(session: Session, document_id: int) -> Document:
    document = session.get(Document, document_id)
    if document is None or document.domain != Domain.HOUSE:
        raise HTTPException(status_code=404, detail="House document not found")
    return document


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
    }


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
