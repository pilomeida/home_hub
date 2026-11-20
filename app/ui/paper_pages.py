"""The pages every category of typed papers shares (PLAN step 8): add a paper (choose its kind, its details, the
file), a paper's page (the file, its details to correct, why it has no reminder yet) and saving its details. A
category gives its own landing page; Identity & Legal, Vehicles, Education and Pets use this."""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.categories.fields import (
    InvalidClassification, build_form_fields, describe_document, load_fields, media_kind_for,
)
from app.categories.registry import document_url, get_spec
from app.db import get_session
from app.models.category import Category
from app.models.document import Document, DocumentSource
from app.problems import file_notices
from app.services.ingestion import Classification, IncomingFile, ingest, update_document_fields
from app.templating import templates

_NON_FIELD_KEYS = {"file", "category"}


def paper_pages(category: Category, prefix: str, landing: Callable[[Session], dict], landing_template: str,
                *, upload_note: str = "") -> APIRouter:
    """The spec is looked up per request, never at import (a test may narrow the registry before the app loads)."""
    router = APIRouter(prefix=prefix, tags=[category.value])

    def _document_or_404(session: Session, document_id: int) -> Document:
        spec = get_spec(category)
        document = session.get(Document, document_id)
        if document is None or document.category != category:
            raise HTTPException(status_code=404, detail={
                "what": "That paper isn't here.", "why": f"It isn't filed under {spec.label} (it may have been re-filed).",
                "next_step": "Look for it with the search box at the top."})
        return document

    def _upload_context(session: Session, paper_kind: Optional[str], values: dict, errors: dict) -> dict:
        spec = get_spec(category)
        choices = [(k.value, k.label) for k in spec.kinds]
        known = paper_kind if any(v == paper_kind for v, _ in choices) else None
        return {"spec": spec, "choices": choices, "category": known, "errors": file_notices(errors),
                "upload_note": upload_note,
                "form_fields": build_form_fields(session, spec, known, values, errors) if known else []}

    def _detail_context(session: Session, document: Document, values: dict, errors: dict) -> dict:
        media_kind = media_kind_for(document.filename)
        spec = get_spec(category)
        return {"spec": spec, "document": document, "description": describe_document(document),
                "media_kind": media_kind.value,
                "form_fields": build_form_fields(session, spec, document.paper_kind, values, errors,
                                                 media_kind=media_kind)}

    @router.get("")
    async def landing_page(request: Request, session: Session = Depends(get_session)):
        return templates.TemplateResponse(request, landing_template, {"spec": get_spec(category), **landing(session)})

    @router.get("/upload")
    async def upload_form(request: Request, category: Optional[str] = None, session: Session = Depends(get_session)):
        return templates.TemplateResponse(request, "domains/upload.html", _upload_context(session, category, {}, {}))

    @router.get("/upload/fields")
    async def upload_fields(request: Request, category: Optional[str] = None, session: Session = Depends(get_session)):
        return templates.TemplateResponse(request, "domains/_field_inputs.html",
                                          _upload_context(session, category, {}, {}))

    @router.post("/upload")
    async def upload(request: Request, session: Session = Depends(get_session)):
        form = await request.form()
        kind = (form.get("category") or "").strip() or None
        values = {k: v for k, v in form.items() if k not in _NON_FIELD_KEYS and isinstance(v, str)}
        upload_file = form.get("file")
        if upload_file is None or isinstance(upload_file, str) or not upload_file.filename:
            return templates.TemplateResponse(
                request, "domains/upload.html",
                _upload_context(session, kind, values, {"file": "No file was chosen to upload"}), status_code=400)
        try:
            result = await ingest(
                session,
                IncomingFile(upload_file.filename, await upload_file.read(), DocumentSource.MANUAL,
                             getattr(request.state, "user_email", None)),
                Classification(category=category, paper_kind=kind, fields=values))
        except InvalidClassification as exc:
            return templates.TemplateResponse(
                request, "domains/upload.html", _upload_context(session, kind, values, exc.errors), status_code=400)
        return RedirectResponse(document_url(result.document) or prefix, status_code=303)

    @router.get("/documents/{document_id}")
    async def document_detail(request: Request, document_id: int, session: Session = Depends(get_session)):
        document = _document_or_404(session, document_id)
        return templates.TemplateResponse(
            request, "domains/detail.html", _detail_context(session, document, load_fields(document), {}))

    @router.post("/documents/{document_id}/fields")
    async def update_fields(request: Request, document_id: int, session: Session = Depends(get_session)):
        document = _document_or_404(session, document_id)
        values = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
        try:
            await update_document_fields(session, document, values)
        except InvalidClassification as exc:
            return templates.TemplateResponse(
                request, "domains/detail.html", _detail_context(session, document, values, exc.errors), status_code=400)
        return RedirectResponse(f"{prefix}/documents/{document.id}", status_code=303)

    return router
