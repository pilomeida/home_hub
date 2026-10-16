"""Health tab: /health/*. Everyone with Hub access sees everything (Pedro, Q5); privacy is about what the AI
and the outbound channels may read, not about who may look."""

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.db import get_session
from app.domains.fields import (
    InvalidClassification, build_form_fields, describe_document, load_fields, media_kind_for,
)
from app.domains.health import areas as areas_svc
from app.domains.health import browse, people as people_svc, store
from app.domains.registry import document_url, get_spec
from app.models.document import Document, DocumentSource
from app.models.domain import Domain
from app.models.health import HealthArea, HealthMeasurement
from app.problems import Problem, file_notices, record_problem
from app.services.ingestion import Classification, IncomingFile, ingest, update_document_fields
from app.templating import templates

router = APIRouter(prefix="/health", tags=["health"])
_NON_FIELD_KEYS = {"file", "category"}


def _document_or_404(session: Session, document_id: int) -> Document:
    document = session.get(Document, document_id)
    if document is None or document.domain != Domain.HEALTH:
        raise HTTPException(status_code=404, detail={
            "what": "Paper not found.", "why": "No Health paper has that number; it may have been deleted, or it belongs to another tab.",
            "next_step": "Open the Health records list and choose the paper from there."})
    return document


def _upload_context(errors: dict) -> dict:
    return {"errors": file_notices(errors)}


def _detail_context(session: Session, document: Document, values: dict, errors: dict) -> dict:
    spec = get_spec(Domain.HEALTH)
    entry = store.entry_for(session, document.id)
    measurements = list(session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)
                                     .order_by(HealthMeasurement.measured_on, HealthMeasurement.test))) if entry else []
    tags = areas_svc.tags_of(session, entry.id) if entry else []
    media_kind = media_kind_for(document.filename)
    return {"document": document, "description": describe_document(document), "media_kind": media_kind.value,
            "entry": entry, "measurements": measurements,
            "area_names": ", ".join(t.name for t in tags if t.kind == "area"),
            "condition_names": ", ".join(t.name for t in tags if t.kind == "condition"),
            "form_fields": build_form_fields(session, spec, document.category, values, errors, media_kind=media_kind)}


@router.get("/records")
async def landing(request: Request, session: Session = Depends(get_session)):
    f = browse.parse_filters(request.query_params)
    return templates.TemplateResponse(request, "health/landing.html", {
        "rows": browse.rows(session, f), "f": f, "facets": browse.facets(session)})


@router.get("/upload")
async def upload_form(request: Request):
    return templates.TemplateResponse(request, "health/upload.html", _upload_context({}))


@router.post("/upload")
async def upload(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    category = (form.get("category") or "").strip() or None
    values = {k: v for k, v in form.items() if k not in _NON_FIELD_KEYS and isinstance(v, str)}
    upload_file = form.get("file")
    if upload_file is None or isinstance(upload_file, str) or not upload_file.filename:
        return templates.TemplateResponse(request, "health/upload.html", _upload_context(
            {"file": "No file was chosen to upload"}), status_code=400)
    try:
        result = await ingest(session, IncomingFile(upload_file.filename, await upload_file.read(), DocumentSource.MANUAL,
                                                    getattr(request.state, "user_email", None)),
                              Classification(domain=Domain.HEALTH, category=category, fields=values))
    except InvalidClassification as exc:
        return templates.TemplateResponse(request, "health/upload.html", _upload_context(exc.errors), status_code=400)
    return RedirectResponse(document_url(result.document) or "/health/records", status_code=303)


@router.get("/documents/{document_id}")
async def document_detail(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    return templates.TemplateResponse(request, "health/detail.html",
                                      _detail_context(session, document, load_fields(document), {}))


@router.post("/documents/{document_id}/fields")
async def update_fields(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    values = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    try:
        await update_document_fields(session, document, values)
    except InvalidClassification as exc:
        return templates.TemplateResponse(request, "health/detail.html",
                                          _detail_context(session, document, values, exc.errors), status_code=400)
    return RedirectResponse(f"/health/documents/{document.id}", status_code=303)


@router.post("/documents/{document_id}/tags")
async def update_tags(document_id: int, areas: str = Form(""), conditions: str = Form(""),
                      session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    entry = store.entry_for(session, document.id)
    if entry is None:
        raise HTTPException(status_code=409, detail={
            "what": "Not tagged.", "why": "This paper has not been read yet, so it has no record to tag.",
            "next_step": "Wait for it to finish, or fix its problem on the Problems page, then try again."})
    split = lambda raw: [p.strip() for p in raw.split(",") if p.strip()]
    areas_svc.set_tags(session, entry, split(areas), split(conditions))
    return RedirectResponse(f"/health/documents/{document.id}", status_code=303)


@router.post("/documents/{document_id}/measurements/{measurement_id}/ok")
async def measurement_ok(document_id: int, measurement_id: int, session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    m = session.get(HealthMeasurement, measurement_id)
    entry = store.entry_for(session, document.id)
    if m is None or entry is None or m.entry_id != entry.id:
        raise HTTPException(status_code=404, detail={
            "what": "Value not found.", "why": "This paper has no such value; the paper may have been read again since the page was opened.",
            "next_step": "Reload the paper's page and mark the value again."})
    m.needs_check = False
    session.add(m)
    session.commit()
    store.sync_entry_from_fields(session, document)
    return RedirectResponse(f"/health/documents/{document.id}", status_code=303)


def _areas_page(request: Request, session: Session, error: Optional[Problem] = None, status_code: int = 200):
    rows = list(session.exec(select(HealthArea).where(HealthArea.merged_into_id.is_(None))
                             .order_by(HealthArea.approved, HealthArea.kind, HealthArea.name)))
    return templates.TemplateResponse(request, "health/areas.html", {"areas": rows, "error": error},
                                      status_code=status_code)


def _people_page(request: Request, session: Session, error: Optional[Problem] = None, status_code: int = 200):
    return templates.TemplateResponse(request, "health/people.html",
                                      {"people": people_svc.all_people(session), "error": error},
                                      status_code=status_code)


def _chosen(raw: Optional[str]) -> Optional[int]:
    try:
        return int(raw) if raw not in (None, "") else None
    except ValueError:
        return None


_NOTHING_CHOSEN = Problem("Not merged.", "Nothing was chosen to merge into.", "Pick the entry to keep, then press Merge.")


@router.get("/areas")
async def areas_page(request: Request, session: Session = Depends(get_session)):
    return _areas_page(request, session)


@router.post("/areas/{area_id}/approve")
async def area_approve(request: Request, area_id: int, session: Session = Depends(get_session)):
    try:
        areas_svc.approve_area(session, area_id)
    except areas_svc.AreaError as exc:
        return _areas_page(request, session, exc.problem, exc.status)
    return RedirectResponse("/health/areas", status_code=303)


@router.post("/areas/{area_id}/rename")
async def area_rename(request: Request, area_id: int, name: str = Form(""), session: Session = Depends(get_session)):
    try:
        areas_svc.rename_area(session, area_id, name)
    except areas_svc.AreaError as exc:
        return _areas_page(request, session, exc.problem, exc.status)
    return RedirectResponse("/health/areas", status_code=303)


@router.post("/areas/{area_id}/merge")
async def area_merge(request: Request, area_id: int, into: Optional[str] = Form(None),
                     session: Session = Depends(get_session)):
    target = _chosen(into)
    if target is None:
        return _areas_page(request, session, _NOTHING_CHOSEN, 400)
    try:
        areas_svc.merge_areas(session, area_id, target)
    except areas_svc.AreaError as exc:
        return _areas_page(request, session, exc.problem, exc.status)
    return RedirectResponse("/health/areas", status_code=303)


@router.get("/people")
async def people_page(request: Request, session: Session = Depends(get_session)):
    return _people_page(request, session)


@router.post("/people/{person_id}/confirm")
async def person_confirm(request: Request, person_id: int, session: Session = Depends(get_session)):
    try:
        people_svc.confirm_person(session, person_id)
    except people_svc.PersonError as exc:
        return _people_page(request, session, exc.problem, exc.status)
    return RedirectResponse("/health/people", status_code=303)


@router.post("/people/{person_id}/merge")
async def person_merge(request: Request, person_id: int, into: Optional[str] = Form(None),
                       session: Session = Depends(get_session)):
    target = _chosen(into)
    if target is None:
        return _people_page(request, session, _NOTHING_CHOSEN, 400)
    try:
        people_svc.merge_people(session, person_id, target)
    except people_svc.PersonError as exc:
        return _people_page(request, session, exc.problem, exc.status)
    return RedirectResponse("/health/people", status_code=303)


@router.post("/documents/{document_id}/delete")
async def delete_document(document_id: int, session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    try:
        await store.discard_document(session, document)
    except Exception as exc:
        session.rollback()
        problem = Problem("Not deleted.", "The Hub could not take everything read from this paper out of its wiki and records, "
                          "so the paper was left as it was.",
                          "Try again in a few minutes. If it keeps failing, tell Pedro: the Problems page has the technical details.",
                          f"/health/documents/{document_id}")
        record_problem(session, area="Health", ref=f"document:{document_id}", problem=problem, technical=f"{type(exc).__name__}: {exc}")
        raise HTTPException(status_code=500, detail={"what": problem.what, "why": problem.why, "next_step": problem.next_step,
                                                     "link": problem.link}) from exc
    return RedirectResponse("/health/records", status_code=303)
