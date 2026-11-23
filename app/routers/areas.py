"""An added area's pages: /areas/<value>/* (PLAN step 12a). One router serves every area Pedro approved, from the
registry (S3: derived, not written per area): the area's papers by kind, a paper's page, saving its details."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.categories.base import CategorySpec
from app.categories.entries import category_entries
from app.categories.fields import InvalidClassification, build_form_fields, describe_document, load_fields, media_kind_for
from app.categories.generic.spec import AREAS_PREFIX
from app.categories.registry import get_spec, is_implemented
from app.db import get_session
from app.models.category import BUILT_IN, Category
from app.models.document import Document
from app.services.ingestion import update_document_fields
from app.templating import templates

router = APIRouter(prefix=AREAS_PREFIX, tags=["areas"])
other_router = APIRouter(tags=["areas"])


def _area_or_404(value: str) -> CategorySpec:
    try:
        category = Category(value)
    except ValueError:
        category = None
    if category is None or value in BUILT_IN or not is_implemented(category):
        raise HTTPException(status_code=404, detail={
            "what": "That area isn't here.", "why": "No area of the Hub has that name.",
            "next_step": "Pick an area from the menu at the top, or look for the paper with the search box."})
    return get_spec(category)


def _document_or_404(session: Session, spec: CategorySpec, document_id: int) -> Document:
    document = session.get(Document, document_id)
    if document is None or document.category != spec.category:
        raise HTTPException(status_code=404, detail={
            "what": "That paper isn't here.", "why": f"It isn't filed under {spec.label} (it may have been re-filed).",
            "next_step": "Look for it with the search box at the top."})
    return document


def _detail_context(session: Session, spec: CategorySpec, document: Document, values: dict, errors: dict) -> dict:
    media_kind = media_kind_for(document.filename)
    return {"spec": spec, "document": document, "description": describe_document(document),
            "media_kind": media_kind.value,
            "form_fields": build_form_fields(session, spec, document.paper_kind, values, errors, media_kind=media_kind)}


@router.get("/{value}")
async def area_landing(request: Request, value: str, session: Session = Depends(get_session)):
    spec = _area_or_404(value)
    papers = []
    for entry in category_entries(session, spec.category):
        if entry.document is None:
            continue
        fields = load_fields(entry.document)
        papers.append({"document": entry.document, "title": fields.get("title") or entry.document.filename,
                       "date": fields.get("paper_date") or "", "about": fields.get("about") or "",
                       "summary": fields.get("summary") or "", "kind": entry.document.paper_kind})
    papers.sort(key=lambda p: (p["date"] or p["document"].created_at.strftime("%Y-%m-%d")), reverse=True)
    sections = [(k.label, [p for p in papers if p["kind"] == k.value]) for k in spec.kinds]
    return templates.TemplateResponse(request, "areas/landing.html",
                                      {"spec": spec, "sections": [(t, ps) for t, ps in sections if ps]})


@router.get("/{value}/documents/{document_id}")
async def document_detail(request: Request, value: str, document_id: int, session: Session = Depends(get_session)):
    spec = _area_or_404(value)
    document = _document_or_404(session, spec, document_id)
    return templates.TemplateResponse(request, "domains/detail.html",
                                      _detail_context(session, spec, document, load_fields(document), {}))


@router.post("/{value}/documents/{document_id}/fields")
async def update_fields(request: Request, value: str, document_id: int, session: Session = Depends(get_session)):
    spec = _area_or_404(value)
    document = _document_or_404(session, spec, document_id)
    values = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    try:
        await update_document_fields(session, document, values)
    except InvalidClassification as exc:
        return templates.TemplateResponse(request, "domains/detail.html",
                                          _detail_context(session, spec, document, values, exc.errors), status_code=400)
    return RedirectResponse(f"{spec.home_url}/documents/{document.id}", status_code=303)


@other_router.get("/other")
@other_router.get("/other/{rest:path}")
async def other_retired(rest: str = ""):
    """"Other papers" was retired in step 12a (it never held a paper on the live Hub): old links lead to All papers."""
    return RedirectResponse("/papers", status_code=308)
