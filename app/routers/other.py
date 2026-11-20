"""Other papers tab: /other/* (PLAN step 1c)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.categories.entries import category_entries
from app.categories.fields import InvalidClassification, build_form_fields, describe_document, load_fields, media_kind_for
from app.categories.other.spec import KIND_ORDER, OTHER_PAPER
from app.categories.registry import document_url, get_spec
from app.db import get_session
from app.models.category import Category
from app.models.document import Document, DocumentSource
from app.problems import file_notices
from app.services.ingestion import Classification, IncomingFile, ingest, update_document_fields
from app.templating import templates

router = APIRouter(prefix="/other", tags=["other"])


def _document_or_404(session: Session, document_id: int) -> Document:
    document = session.get(Document, document_id)
    if document is None or document.category != Category.OTHER:
        raise HTTPException(status_code=404, detail={
            "what": "That paper isn't here.", "why": "It isn't filed under Other papers (it may have been re-filed).",
            "next_step": "Look for it with the search box at the top."})
    return document


def _kind(value) -> str:
    return value if value in KIND_ORDER else OTHER_PAPER


def _upload_context(session: Session, values: dict, errors: dict) -> dict:
    spec = get_spec(Category.OTHER)
    kind = _kind(values.get("category"))
    return {"errors": file_notices(errors), "kind": kind, "choices": [(k.value, k.label) for k in spec.kinds],
            "form_fields": build_form_fields(session, spec, kind, values, errors)}


def _detail_context(session: Session, document: Document, values: dict, errors: dict) -> dict:
    media_kind = media_kind_for(document.filename)
    return {"document": document, "description": describe_document(document), "media_kind": media_kind.value,
            "form_fields": build_form_fields(session, get_spec(Category.OTHER), document.paper_kind, values, errors,
                                             media_kind=media_kind)}


@router.get("")
async def other_landing(request: Request, session: Session = Depends(get_session)):
    papers = []
    for entry in category_entries(session, Category.OTHER):
        if entry.document is None:
            continue
        fields = load_fields(entry.document)
        papers.append({"document": entry.document, "title": fields.get("title") or entry.document.filename,
                       "date": fields.get("paper_date") or "", "about": fields.get("about") or "",
                       "summary": fields.get("summary") or "", "kind": _kind(entry.document.paper_kind)})
    papers.sort(key=lambda p: (p["date"] or p["document"].created_at.strftime("%Y-%m-%d")), reverse=True)
    spec = get_spec(Category.OTHER)
    sections = [(spec.kind_label(k) + "s", [p for p in papers if p["kind"] == k]) for k in KIND_ORDER]
    return templates.TemplateResponse(request, "other/landing.html",
                                      {"papers": papers, "sections": [(t, ps) for t, ps in sections if ps]})


@router.get("/upload")
async def upload_form(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "other/upload.html", _upload_context(session, {}, {}))


@router.post("/upload")
async def upload(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    values = {k: v for k, v in form.items() if k != "file" and isinstance(v, str)}
    kind = _kind(values.pop("category", None))
    upload_file = form.get("file")
    if upload_file is None or isinstance(upload_file, str) or not upload_file.filename:
        return templates.TemplateResponse(request, "other/upload.html",
                                          _upload_context(session, {**values, "category": kind}, {"file": "No file was chosen to upload"}),
                                          status_code=400)
    try:
        result = await ingest(session, IncomingFile(upload_file.filename, await upload_file.read(), DocumentSource.MANUAL,
                                                    getattr(request.state, "user_email", None)),
                              Classification(Category.OTHER, kind, values))
    except InvalidClassification as exc:
        return templates.TemplateResponse(request, "other/upload.html", _upload_context(session, {**values, "category": kind}, exc.errors),
                                          status_code=400)
    return RedirectResponse(document_url(result.document) or "/other", status_code=303)


@router.get("/documents/{document_id}")
async def document_detail(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    return templates.TemplateResponse(request, "other/detail.html",
                                      _detail_context(session, document, load_fields(document), {}))


@router.post("/documents/{document_id}/fields")
async def update_fields(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    values = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    try:
        await update_document_fields(session, document, values)
    except InvalidClassification as exc:
        return templates.TemplateResponse(request, "other/detail.html",
                                          _detail_context(session, document, values, exc.errors), status_code=400)
    return RedirectResponse(f"/other/documents/{document.id}", status_code=303)
