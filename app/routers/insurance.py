"""Insurance tab: /insurance/*."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.db import get_session
from app.domains.fields import (
    InvalidClassification, build_form_fields, describe_document, load_fields, media_kind_for,
)
from app.domains.insurance.categories import POLICY_PAGE_TYPE
from app.domains.insurance.policies import policy_sections
from app.domains.registry import document_url, get_spec
from app.models.document import Document, DocumentSource
from app.models.domain import Domain
from app.services.ingestion import Classification, IncomingFile, ingest, update_document_fields
from app.services.wiki_store import PageRef, find_page, normalize_entity_key
from app.templating import templates

router = APIRouter(prefix="/insurance", tags=["insurance"])

_NON_FIELD_KEYS = {"file", "category"}


def _upload_context(session: Session, category: Optional[str], values: dict, errors: dict) -> dict:
    spec = get_spec(Domain.INSURANCE)
    known = category if category and any(c.value == category for c in spec.categories) else None
    return {
        "categories": spec.categories, "category": known, "errors": errors,
        "form_fields": build_form_fields(session, spec, known, values, errors) if known else [],
    }


def _document_or_404(session: Session, document_id: int) -> Document:
    document = session.get(Document, document_id)
    if document is None or document.domain != Domain.INSURANCE:
        raise HTTPException(status_code=404, detail="Insurance document not found")
    return document


def _detail_context(session: Session, document: Document, values: dict, errors: dict) -> dict:
    spec = get_spec(Domain.INSURANCE)
    fields = load_fields(document)
    page = None
    if fields.get("policy_number"):
        page = find_page(session, PageRef(POLICY_PAGE_TYPE, fields["policy_number"], normalize_entity_key(fields["policy_number"])))
    media_kind = media_kind_for(document.filename)
    return {
        "document": document, "description": describe_document(document), "media_kind": media_kind.value,
        "form_fields": build_form_fields(session, spec, document.category, values, errors, media_kind=media_kind),
        "policy_page": page,
    }


@router.get("")
async def insurance_landing(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "insurance/landing.html", {"sections": policy_sections(session)})


@router.get("/upload")
async def upload_form(request: Request, category: Optional[str] = None, session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "insurance/upload.html", _upload_context(session, category, {}, {}))


@router.get("/upload/fields")
async def upload_fields(request: Request, category: Optional[str] = None, session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "domains/_field_inputs.html", _upload_context(session, category, {}, {}))


@router.post("/upload")
async def upload(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    category = (form.get("category") or "").strip() or None
    values = {k: v for k, v in form.items() if k not in _NON_FIELD_KEYS and isinstance(v, str)}
    upload_file = form.get("file")
    if upload_file is None or isinstance(upload_file, str) or not upload_file.filename:
        return templates.TemplateResponse(
            request, "insurance/upload.html",
            _upload_context(session, category, values, {"file": "Choose a file to upload"}), status_code=400)
    try:
        result = await ingest(
            session,
            IncomingFile(upload_file.filename, await upload_file.read(), DocumentSource.MANUAL,
                         getattr(request.state, "user_email", None)),
            Classification(domain=Domain.INSURANCE, category=category, fields=values))
    except InvalidClassification as exc:
        return templates.TemplateResponse(
            request, "insurance/upload.html", _upload_context(session, category, values, exc.errors), status_code=400)
    return RedirectResponse(document_url(result.document) or "/insurance", status_code=303)


@router.get("/documents/{document_id}")
async def document_detail(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    return templates.TemplateResponse(
        request, "insurance/detail.html", _detail_context(session, document, load_fields(document), {}))


@router.post("/documents/{document_id}/fields")
async def update_fields(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    values = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    try:
        await update_document_fields(session, document, values)
    except InvalidClassification as exc:
        return templates.TemplateResponse(
            request, "insurance/detail.html", _detail_context(session, document, values, exc.errors), status_code=400)
    return RedirectResponse(f"/insurance/documents/{document.id}", status_code=303)
