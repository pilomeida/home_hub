"""Health tab: /health/*. Everyone with Hub access sees everything (Pedro, Q5); privacy is about what the AI
and the outbound channels may read, not about who may look."""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.db import get_session, get_session_factory
from app.categories.fields import (
    InvalidClassification, build_form_fields, describe_document, dump_fields, load_fields, media_kind_for,
)
from app.categories.health import areas as areas_svc
from app.categories.health import conditions as conditions_svc
from app.categories.health import follow_ups as follow_ups_svc
from app.categories.health import care as care_svc
from app.categories.health import browse, outlines, people as people_svc, store
from app.categories.health import topics as topics_svc
from app.categories.health import tests as tests_svc
from app.categories.health.text import PATH_SEP
from app.categories.registry import document_url, get_spec
from app.services.wiki_engine import withdraw_from_wiki
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.category import Category
from app.models.health import HealthMeasurement, HealthTopic
from app.problems import Problem, file_notices, is_health_bill_refusal, record_problem
from app.services.ingestion import (
    kept_on_reread,
    Classification, IncomingFile, file_for_later, finalize_document, mark_needs_attention, update_document_fields,
)
from app.services import health_views
from app.services.paper_uses import PaperInUse
from app.templating import templates

router = APIRouter(prefix="/health", tags=["health"])
_NON_FIELD_KEYS = {"file", "category"}


def spec_for_health():
    return get_spec(Category.HEALTH)


def _document_or_404(session: Session, document_id: int) -> Document:
    document = session.get(Document, document_id)
    if document is None or document.category != Category.HEALTH:
        raise HTTPException(status_code=404, detail={
            "what": "Paper not found.", "why": "No Health paper has that number; it may have been deleted, or it belongs to another tab.",
            "next_step": "Open the Health records list and choose the paper from there."})
    return document


def _upload_context(errors: dict) -> dict:
    return {"errors": file_notices(errors)}


def _detail_context(session: Session, document: Document, values: dict, errors: dict) -> dict:
    spec = get_spec(Category.HEALTH)
    fields = load_fields(document)
    entry = store.entry_for(session, document.id)
    measurements = health_views.measurement_view(health_views.measurements_of(session, entry)) if entry else []
    tags = areas_svc.tags_of(session, entry.id) if entry else []
    is_report = bool(entry and entry.layout == "report")
    media_kind = media_kind_for(document.filename)
    description = describe_document(document)
    return {"document": document, "description": description,
            "bill_refused": document.status == DocumentStatus.NEEDS_ATTENTION and is_health_bill_refusal(document.failure_reason), "media_kind": media_kind.value,
            "entry": entry, "measurements": measurements, "is_report": is_report, "path_sep": PATH_SEP,
            "outline": health_views.outline_view(session, entry, measurements) if is_report else [],
            "metrics": [row for row in measurements if row["m"].value is not None] if is_report else [],
            "check_notes": store.notes_of(entry) if entry else [],
            "conditions_box": conditions_svc.entry_box(session, entry),
            "area_names": ", ".join(t.name for t in tags if t.kind == "area"),
            "condition_names": ", ".join(t.name for t in tags if t.kind == "condition"),
            "why": {k: fields.get(k, "") for k in ("indication", "clinical_notes", "requested_by", "performed_by", "details")},
            "form_fields": build_form_fields(session, spec, document.paper_kind, values, errors, media_kind=media_kind)}


async def _read_in_background(session_factory, document_id: int) -> None:
    """Read and file one paper. Runs after the response, in its own session: the reading can take minutes."""
    with session_factory() as session:
        document = session.get(Document, document_id)
        if document is None or document.status == DocumentStatus.PROCESSED:
            return
        try:
            await finalize_document(session, document, Classification(Category.HEALTH, document.paper_kind, load_fields(document)))
        except BaseException as exc:      # also a cancelled task: the paper must never be left looking unread
            session.rollback()
            session.refresh(document)
            mark_needs_attention(session, document, f"Health processing failed: {type(exc).__name__} {exc}".strip())
            if not isinstance(exc, Exception):
                raise


@router.get("/family")
async def health_home(request: Request, session: Session = Depends(get_session)):
    """Health (step 10f): a tile per person, the follow-ups across the family, papers by area."""
    from app.services.health_page import health_home as build
    return templates.TemplateResponse(request, "health/home.html", {"h": build(session)})


@router.get("/people/{person_id}")
async def person_page(request: Request, person_id: int, session: Session = Depends(get_session)):
    """A person's page (step 10f): conditions, results against the reference range, timeline."""
    from app.services.health_page import person_page as build
    page = build(session, person_id)
    if page is None:
        raise HTTPException(status_code=404, detail={
            "what": "Nothing to show.", "why": "That person is no longer listed (perhaps merged into someone else).",
            "next_step": "Go back to Health and open them from there."})
    return templates.TemplateResponse(request, "health/person.html", {"p": page})


@router.get("/records")
async def landing(request: Request, session: Session = Depends(get_session)):
    f = browse.parse_filters(request.query_params)
    waiting = health_views.papers_waiting(session)
    return templates.TemplateResponse(request, "health/landing.html", {
        "rows": browse.rows(session, f), "f": f, "facets": browse.facets(session), "waiting": waiting})


@router.get("/upload")
async def upload_form(request: Request):
    return templates.TemplateResponse(request, "health/upload.html", _upload_context({}))


@router.post("/upload")
async def upload(request: Request, background_tasks: BackgroundTasks, session: Session = Depends(get_session),
                 session_factory=Depends(get_session_factory)):
    form = await request.form()
    category = (form.get("category") or "").strip() or None
    values = {k: v for k, v in form.items() if k not in _NON_FIELD_KEYS and isinstance(v, str)}
    upload_file = form.get("file")
    if upload_file is None or isinstance(upload_file, str) or not upload_file.filename:
        return templates.TemplateResponse(request, "health/upload.html", _upload_context(
            {"file": "No file was chosen to upload"}), status_code=400)
    try:
        result = file_for_later(session, IncomingFile(upload_file.filename, await upload_file.read(), DocumentSource.MANUAL,
                                                      getattr(request.state, "user_email", None)),
                                Classification(category=Category.HEALTH, paper_kind=category, fields=values))
    except InvalidClassification as exc:
        return templates.TemplateResponse(request, "health/upload.html", _upload_context(exc.errors), status_code=400)
    if not result.duplicate:       # the reading takes minutes: the person lands on the paper's page, which shows its progress
        background_tasks.add_task(_read_in_background, session_factory, result.document.id)
    return RedirectResponse(document_url(result.document) or "/health/records", status_code=303)


async def prepare_reread(session: Session, document: Document) -> None:
    """The steps behind "Read again": take back everything read from a filed paper (calendar entry, wiki claims,
    results, tags), clear the fields the reader filled so they start again from the new reading (the ones a person
    typed or changed stay), and mark it pending."""
    if document.status == DocumentStatus.PROCESSED:
        await spec_for_health().handler.withdraw(session, document)
        await withdraw_from_wiki(session, document, description=f"{document.filename} read again")
        document.fields_json = kept_on_reread(document)     # what a person typed stays (step 5d); the rest is read again
    elif document.status not in (DocumentStatus.PENDING, DocumentStatus.NEEDS_ATTENTION):
        raise HTTPException(status_code=409, detail={
            "what": "Not read again.", "why": "This paper was deleted, so there is nothing to read.",
            "next_step": "Upload it again if you need it."})
    document.status = DocumentStatus.PENDING
    document.failure_reason = None
    session.add(document)
    session.commit()


@router.post("/documents/{document_id}/reread")
async def reread(document_id: int, background_tasks: BackgroundTasks, session: Session = Depends(get_session),
                 session_factory=Depends(get_session_factory)):
    document = _document_or_404(session, document_id)
    await prepare_reread(session, document)
    background_tasks.add_task(_read_in_background, session_factory, document.id)
    return RedirectResponse(f"/health/documents/{document.id}", status_code=303)


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


@router.post("/documents/{document_id}/why")
async def update_why(document_id: int, indication: str = Form(""), session: Session = Depends(get_session)):
    """The person's own words for why this paper was made (for papers that do not say). Kept as the field `indication`."""
    document = _document_or_404(session, document_id)
    if document.status != DocumentStatus.PROCESSED:       # a reading in progress would overwrite it; an unread paper has no box to write in
        why, step = {
            DocumentStatus.PENDING: ("The paper is still being read, and the reading would overwrite it.",
                                     "Wait until the reading is done, then write it again."),
            DocumentStatus.NEEDS_ATTENTION: ("The paper has not been read.",
                                             "Read it again, or write the reason in the boxes below."),
        }.get(document.status, ("This paper was deleted.", "Upload it again if you need it."))
        raise HTTPException(status_code=409, detail={"what": "Not saved.", "why": why, "next_step": step})
    fields = load_fields(document)
    fields["indication"] = indication.strip()
    document.fields_json = dump_fields(fields)
    session.add(document)
    session.commit()
    if store.entry_for(session, document.id) is not None:
        store.sync_entry_from_fields(session, document)
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
    if conditions_svc.suggest_for_entry(session, entry):
        session.commit()
    return RedirectResponse(f"/health/documents/{document.id}", status_code=303)


def _by(request: Request) -> Optional[str]:
    return getattr(request.state, "user_email", None)


def _condition_problem(exc: conditions_svc.ConditionError, status: int = 409) -> HTTPException:
    p = exc.problem
    return HTTPException(status_code=status, detail={"what": p.what, "why": p.why, "next_step": p.next_step})


@router.post("/documents/{document_id}/conditions")
async def decide_conditions(request: Request, document_id: int, action: str = Form(...),
                            condition_id: Optional[int] = Form(None), name: str = Form(""),
                            session: Session = Depends(get_session)):
    """The paper's conditions box (step 7a): confirm or reject a suggestion, pick another, none of these, or create a
    new condition from this paper."""
    document = _document_or_404(session, document_id)
    entry = store.entry_for(session, document.id)
    if entry is None or entry.person_id is None:
        raise HTTPException(status_code=409, detail={
            "what": "Not saved.", "why": "This paper has no person yet, so it cannot be about one of their conditions.",
            "next_step": "Fill in whose paper it is in the details below, then try again."})
    try:
        if action in ("confirm", "reject") and condition_id is not None:
            conditions_svc.decide(session, entry.id, condition_id,
                                  conditions_svc.CONFIRMED if action == "confirm" else conditions_svc.REJECTED, _by(request))
        elif action == "none":
            conditions_svc.none_of_these(session, entry.id, _by(request))
        elif action == "create":
            conditions_svc.create(session, entry.person_id, name, _by(request), confirm_entry_id=entry.id)
        else:
            raise HTTPException(status_code=400, detail={
                "what": "Not saved.", "why": "The button pressed did not say what to do.",
                "next_step": "Reload the page and press the button again."})
    except conditions_svc.ConditionError as exc:
        raise _condition_problem(exc) from None
    return RedirectResponse(f"/health/documents/{document.id}#conditions", status_code=303)


@router.post("/people/{person_id}/conditions")
async def create_condition(request: Request, person_id: int, name: str = Form(""), started_on: str = Form(""),
                           session: Session = Depends(get_session)):
    try:
        condition = conditions_svc.create(session, person_id, name, _by(request), started_on=_date_or_none(started_on))
    except conditions_svc.ConditionError as exc:
        raise _condition_problem(exc) from None
    return RedirectResponse(f"/health/conditions/{condition.id}", status_code=303)


def _date_or_none(raw: str):
    from datetime import date as _date
    try:
        return _date.fromisoformat(raw.strip()) if raw and raw.strip() else None
    except ValueError:
        raise HTTPException(status_code=400, detail={
            "what": "Not saved.", "why": f"“{raw}” is not a date.", "next_step": "Write it as yyyy-mm-dd."}) from None


@router.get("/conditions/{condition_id}")
async def condition_page(request: Request, condition_id: int, session: Session = Depends(get_session)):
    from app.models.health import HealthCondition, HealthPerson
    condition = session.get(HealthCondition, condition_id)
    if condition is None:
        raise HTTPException(status_code=404, detail={
            "what": "Not found.", "why": "That condition is no longer listed.",
            "next_step": "Open the person's page to see their conditions."})
    papers = [(e, session.get(Document, e.document_id)) for e in conditions_svc.papers_of(session, condition.id)]
    suggested = [(e, session.get(Document, e.document_id))
                 for e in conditions_svc.papers_of(session, condition.id, conditions_svc.SUGGESTED)]
    from datetime import date as _date
    return templates.TemplateResponse(request, "health/condition.html", {
        "c": condition, "person": session.get(HealthPerson, condition.person_id), "papers": papers,
        "suggested": suggested, "statuses": conditions_svc.STATUSES,
        "follow_ups": follow_ups_svc.open_ones(session, _date.today(), condition_id=condition.id),
        "remind_before": follow_ups_svc.REMIND_BEFORE, "timeline": care_svc.timeline(session, condition.id),
        "medications": care_svc.medications(session, condition.id),
        "booked": care_svc.visits(session, condition.id, "booked"), "note_kinds": care_svc.NOTE_KINDS})


def _care(call):
    try:
        return call()
    except care_svc.CareError as exc:
        p = exc.problem
        raise HTTPException(status_code=400, detail={"what": p.what, "why": p.why, "next_step": p.next_step}) from None


def _float_or_none(raw: str):
    try:
        return float(raw.replace(",", ".")) if raw and raw.strip() else None
    except ValueError:
        raise HTTPException(status_code=400, detail={"what": "Not saved.", "why": f"“{raw}” is not a number.",
                                                     "next_step": "Type the value as a number, e.g. 7.2."}) from None


@router.post("/conditions/{condition_id}/visits")
async def add_visit(request: Request, condition_id: int, visit_on: str = Form(""), clinic: str = Form(""),
                    doctor: str = Form(""), status: str = Form("booked"), observations: str = Form(""),
                    plan_changes: str = Form(""), session: Session = Depends(get_session)):
    _care(lambda: care_svc.add_visit(session, condition_id, visit_on=_date_or_none(visit_on), clinic=clinic,
                                     doctor=doctor, status=status, observations=observations,
                                     plan_changes=plan_changes, by=_by(request)))
    return RedirectResponse(f"/health/conditions/{condition_id}", status_code=303)


@router.post("/visits/{visit_id}/done")
async def visit_done(visit_id: int, observations: str = Form(""), plan_changes: str = Form(""),
                     next_appointment_on: str = Form(""), session: Session = Depends(get_session)):
    visit = _care(lambda: care_svc.visit_done(session, visit_id, observations=observations, plan_changes=plan_changes,
                                              next_appointment_on=_date_or_none(next_appointment_on)))
    return RedirectResponse(f"/health/conditions/{visit.condition_id}", status_code=303)


@router.post("/conditions/{condition_id}/notes")
async def add_note(request: Request, condition_id: int, noted_on: str = Form(""), kind: str = Form("note"),
                   text: str = Form(""), value: str = Form(""), unit: str = Form(""),
                   session: Session = Depends(get_session)):
    _care(lambda: care_svc.add_note(session, condition_id, noted_on=_date_or_none(noted_on), kind=kind, text=text,
                                    value=_float_or_none(value), unit=unit, by=_by(request)))
    return RedirectResponse(f"/health/conditions/{condition_id}", status_code=303)


@router.post("/conditions/{condition_id}/medications")
async def add_medication(request: Request, condition_id: int, name: str = Form(""), dose: str = Form(""),
                         frequency: str = Form(""), started_on: str = Form(""),
                         session: Session = Depends(get_session)):
    _care(lambda: care_svc.add_medication(session, condition_id, name=name, dose=dose, frequency=frequency,
                                          started_on=_date_or_none(started_on), by=_by(request)))
    return RedirectResponse(f"/health/conditions/{condition_id}", status_code=303)


@router.post("/medications/{medication_id}/change")
async def change_medication(request: Request, medication_id: int, changed_on: str = Form(""), dose: str = Form(""),
                            frequency: str = Form(""), action: str = Form("change"),
                            session: Session = Depends(get_session)):
    med = _care(lambda: care_svc.change_medication(session, medication_id, changed_on=_date_or_none(changed_on),
                                                   dose=dose, frequency=frequency, stop=action == "stop",
                                                   by=_by(request)))
    return RedirectResponse(f"/health/conditions/{med.condition_id}", status_code=303)


@router.post("/conditions/{condition_id}/follow-ups")
async def add_follow_up(request: Request, condition_id: int, what: str = Form(""), due_on: str = Form(""),
                        session: Session = Depends(get_session)):
    try:
        follow_ups_svc.add(session, 0, what, _date_or_none(due_on), _by(request), condition_id=condition_id)
    except follow_ups_svc.FollowUpError as exc:
        p = exc.problem
        raise HTTPException(status_code=400, detail={"what": p.what, "why": p.why, "next_step": p.next_step}) from None
    return RedirectResponse(f"/health/conditions/{condition_id}", status_code=303)


@router.post("/follow-ups/{follow_up_id}/dismiss")
async def dismiss_follow_up(follow_up_id: int, back: str = Form("/health/family"),
                            session: Session = Depends(get_session)):
    from app.services.who import safe_back
    try:
        follow_ups_svc.dismiss(session, follow_up_id)
    except follow_ups_svc.FollowUpError as exc:
        p = exc.problem
        raise HTTPException(status_code=404, detail={"what": p.what, "why": p.why, "next_step": p.next_step}) from None
    return RedirectResponse(safe_back(back), status_code=303)


@router.post("/conditions/{condition_id}")
async def update_condition(condition_id: int, status: str = Form("followed"), started_on: str = Form(""),
                           started_note: str = Form(""), followed_by: str = Form(""),
                           session: Session = Depends(get_session)):
    try:
        conditions_svc.update(session, condition_id, status=status, started_on=_date_or_none(started_on),
                              started_note=started_note, followed_by=followed_by)
    except conditions_svc.ConditionError as exc:
        raise _condition_problem(exc, 404) from None
    return RedirectResponse(f"/health/conditions/{condition_id}", status_code=303)


@router.post("/documents/{document_id}/outline/ok")
async def outline_ok(request: Request, document_id: int, session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    entry = store.entry_for(session, document.id)
    if entry is None:
        raise HTTPException(status_code=409, detail={
            "what": "Outline not confirmed.", "why": "This paper has not been read yet, so it has no outline.",
            "next_step": "Wait for it to finish, or fix its problem on the Problems page, then try again."})
    try:
        outlines.confirm_outline(session, entry, by=getattr(request.state, "user_email", None))
    except outlines.OutlineError as exc:
        raise HTTPException(status_code=exc.status, detail={"what": exc.problem.what, "why": exc.problem.why,
                                                            "next_step": exc.problem.next_step}) from exc
    return RedirectResponse(f"/health/documents/{document.id}", status_code=303)


@router.post("/documents/{document_id}/notes/ok")
async def notes_ok(document_id: int, session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    store.clear_notes(session, document)
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
    rows = health_views.live_areas(session)
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


@router.get("/people/{person_id}/results")
async def person_results(request: Request, person_id: int, session: Session = Depends(get_session)):
    """One person's lab results, one line per test whatever lab printed it (step 5b)."""
    from app.models.health import HealthPerson
    person = session.get(HealthPerson, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail={
            "what": "Nothing to show.", "why": "That person is no longer listed (perhaps merged into someone else).",
            "next_step": "Go back to People and open them from there."})
    return templates.TemplateResponse(request, "health/results.html",
                                      {"person": person, "lines": tests_svc.results_of(session, person_id)})


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


def _topics_page(request: Request, session: Session, error: Optional[Problem] = None, status_code: int = 200):
    return templates.TemplateResponse(request, "health/topics.html", {"topics": health_views.topic_rows(session), "error": error},
                                      status_code=status_code)


@router.get("/topics")
async def topics_page(request: Request, session: Session = Depends(get_session)):
    return _topics_page(request, session)


@router.post("/topics/{topic_id}/approve")
async def topic_approve(request: Request, topic_id: int, session: Session = Depends(get_session)):
    try:
        topics_svc.approve_topic(session, topic_id)
    except topics_svc.TopicError as exc:
        return _topics_page(request, session, exc.problem, exc.status)
    return RedirectResponse("/health/topics", status_code=303)


@router.post("/topics/{topic_id}/rename")
async def topic_rename(request: Request, topic_id: int, name: str = Form(""), session: Session = Depends(get_session)):
    try:
        topics_svc.rename_topic(session, topic_id, name)
    except topics_svc.TopicError as exc:
        return _topics_page(request, session, exc.problem, exc.status)
    return RedirectResponse("/health/topics", status_code=303)


@router.post("/topics/{topic_id}/merge")
async def topic_merge(request: Request, topic_id: int, into: Optional[str] = Form(None),
                      session: Session = Depends(get_session)):
    target = _chosen(into)
    if target is None:
        return _topics_page(request, session, _NOTHING_CHOSEN, 400)
    try:
        topics_svc.merge_topics(session, topic_id, target)
    except topics_svc.TopicError as exc:
        return _topics_page(request, session, exc.problem, exc.status)
    return RedirectResponse("/health/topics", status_code=303)


@router.get("/topics/{topic_id}")
async def topic_thread(request: Request, topic_id: int, person: Optional[str] = None,
                       session: Session = Depends(get_session)):
    topic = session.get(HealthTopic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail={
            "what": "Topic not found.", "why": "No topic has that number; it may have been merged into another one or removed.",
            "next_step": "Open the Topics page and choose the topic from there."})
    counts = dict(topics_svc.people_of(session, topic.id))          # a merged-away topic opens the one it was folded into
    people = [p for p in people_svc.all_people(session) if p.id in counts]
    people.sort(key=lambda p: (-counts[p.id], p.id))
    wanted = _chosen(person)
    chosen = wanted if wanted in counts and any(p.id == wanted for p in people) else (people[0].id if people else None)
    found = topics_svc.thread(session, topic.id, chosen)
    return templates.TemplateResponse(request, "health/topic.html", {
        "topic": found["topic"], "path": found["path"], "people": people, "chosen": chosen,
        "conclusions": health_views.conclusions(session, found["topic"], found["entries"]), "metrics": found["metrics"]})


@router.post("/documents/{document_id}/delete")
async def delete_document(document_id: int, session: Session = Depends(get_session)):
    document = _document_or_404(session, document_id)
    try:
        await store.discard_document(session, document)
    except PaperInUse as refused:
        raise HTTPException(status_code=409, detail=refused.problem.as_detail()) from None
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
