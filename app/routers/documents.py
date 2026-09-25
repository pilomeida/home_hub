"""Generic Edit / re-file screens for any finalized document or record:
change domain, category and fields, validated through the registry and the
ingestion core's re-filing path. Domain-agnostic by construction."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.db import get_session
from app.domains.entries import record_for_document
from app.domains.fields import InvalidClassification, build_form_fields, load_fields, media_kind_for
from app.domains.registry import document_url, get_spec, implemented_domains, is_implemented, record_url
from app.models.document import Document
from app.models.domain import Domain
from app.models.record import Record
from app.services.ingestion import Classification, RefileRefusedError, refile_document, refile_record
from app.templating import templates

router = APIRouter(tags=["documents"])

_NON_FIELD_KEYS = {"domain", "category"}


def _domain_or_none(value: Optional[str]) -> Optional[Domain]:
    try:
        domain = Domain(value) if value else None
    except ValueError:
        return None
    return domain if is_implemented(domain) else None


def _context(session: Session, *, action: str, title: str, has_file: bool, filename: Optional[str],
             domain: Optional[Domain], category: Optional[str], values: dict, errors: dict,
             refused: Optional[str] = None) -> dict:
    spec = get_spec(domain) if domain else None
    media_kind = media_kind_for(filename) if filename else None
    return {
        "action": action, "title": title, "has_file": has_file,
        "domains": implemented_domains(), "domain": domain, "category": category,
        "categories": spec.categories if spec else (),
        "form_fields": build_form_fields(session, spec, category, values, errors, media_kind=media_kind) if spec and category else [],
        "errors": errors, "refused": refused,
    }


def _form_values(form) -> tuple[Optional[Domain], Optional[str], dict]:
    values = {k: v for k, v in form.items() if k not in _NON_FIELD_KEYS and isinstance(v, str)}
    return _domain_or_none(form.get("domain")), (form.get("category") or "").strip() or None, values


def _finalized_document_or_404(session: Session, document_id: int) -> Document:
    document = session.get(Document, document_id)
    if document is None or document.domain is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def _active_record_or_404(session: Session, record_id: int) -> Record:
    record = session.get(Record, record_id)
    if record is None or record.retired_at is not None:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


def _home_of(session: Session, result) -> str:
    if isinstance(result, Record):
        return record_url(result) or "/"
    attached = record_for_document(session, result)
    return (record_url(attached) if attached else document_url(result)) or "/"


@router.get("/documents/edit/categories")
async def category_options(request: Request, domain: Optional[str] = None):
    parsed = _domain_or_none(domain)
    categories = get_spec(parsed).categories if parsed else ()
    return templates.TemplateResponse(request, "documents/_category_select.html",
                                      {"categories": categories, "category": None})


@router.get("/documents/edit/fields")
async def field_inputs(request: Request, domain: Optional[str] = None, category: Optional[str] = None,
                       session: Session = Depends(get_session)):
    parsed = _domain_or_none(domain)
    spec = get_spec(parsed) if parsed else None
    known = category if spec and any(c.value == category for c in spec.categories) else None
    form_fields = build_form_fields(session, spec, known, {}, {}) if known else []
    return templates.TemplateResponse(request, "domains/_field_inputs.html", {"form_fields": form_fields})


@router.get("/documents/{document_id}/edit")
async def edit_document(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = _finalized_document_or_404(session, document_id)
    attached = record_for_document(session, document)
    if attached is not None:
        return RedirectResponse(f"/records/{attached.id}/edit", status_code=303)
    return templates.TemplateResponse(request, "documents/edit.html", _context(
        session, action=f"/documents/{document.id}/edit", title=document.filename, has_file=True,
        filename=document.filename, domain=document.domain, category=document.category,
        values=load_fields(document), errors={},
    ))


@router.post("/documents/{document_id}/edit")
async def refile_document_route(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = _finalized_document_or_404(session, document_id)
    domain, category, values = _form_values(await request.form())
    base = dict(action=f"/documents/{document.id}/edit", title=document.filename, has_file=True,
                filename=document.filename, domain=domain, category=category, values=values)
    if domain is None:
        return templates.TemplateResponse(request, "documents/edit.html",
                                          _context(session, **base, errors={"domain": "Choose a domain"}), status_code=400)
    try:
        result = await refile_document(session, document, Classification(domain, category, values))
    except InvalidClassification as exc:
        return templates.TemplateResponse(request, "documents/edit.html",
                                          _context(session, **base, errors=exc.errors), status_code=400)
    except RefileRefusedError as exc:
        return templates.TemplateResponse(request, "documents/edit.html",
                                          _context(session, **base, errors={}, refused=str(exc)), status_code=409)
    return RedirectResponse(_home_of(session, result), status_code=303)


@router.get("/records/{record_id}/edit")
async def edit_record(request: Request, record_id: int, session: Session = Depends(get_session)):
    record = _active_record_or_404(session, record_id)
    attachment = session.get(Document, record.document_id) if record.document_id else None
    return templates.TemplateResponse(request, "documents/edit.html", _context(
        session, action=f"/records/{record.id}/edit", title=f"Record #{record.id}", has_file=attachment is not None,
        filename=attachment.filename if attachment else None, domain=record.domain, category=record.category,
        values=load_fields(record), errors={},
    ))


@router.post("/records/{record_id}/edit")
async def refile_record_route(request: Request, record_id: int, session: Session = Depends(get_session)):
    record = _active_record_or_404(session, record_id)
    attachment = session.get(Document, record.document_id) if record.document_id else None
    domain, category, values = _form_values(await request.form())
    base = dict(action=f"/records/{record.id}/edit", title=f"Record #{record.id}", has_file=attachment is not None,
                filename=attachment.filename if attachment else None, domain=domain, category=category, values=values)
    if domain is None:
        return templates.TemplateResponse(request, "documents/edit.html",
                                          _context(session, **base, errors={"domain": "Choose a domain"}), status_code=400)
    try:
        result = await refile_record(session, record, Classification(domain, category, values))
    except InvalidClassification as exc:
        return templates.TemplateResponse(request, "documents/edit.html",
                                          _context(session, **base, errors=exc.errors), status_code=400)
    except RefileRefusedError as exc:
        return templates.TemplateResponse(request, "documents/edit.html",
                                          _context(session, **base, errors={}, refused=str(exc)), status_code=409)
    return RedirectResponse(_home_of(session, result), status_code=303)
