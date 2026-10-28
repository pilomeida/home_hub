"""Health tab: /health/*. Everyone with Hub access sees everything (Pedro, Q5); privacy is about what the AI
and the outbound channels may read, not about who may look."""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.db import get_session, get_session_factory
from app.domains.fields import (
    InvalidClassification, build_form_fields, describe_document, dump_fields, load_fields, media_kind_for,
)
from app.domains.health import areas as areas_svc
from app.domains.health.categories import LEGACY_CATEGORIES, LEGACY_LABELS
from app.domains.health import browse, outlines, people as people_svc, store
from app.domains.health import topics as topics_svc
from app.domains.health.text import PATH_SEP, match_key, reference_for_display
from app.domains.registry import document_url, get_spec
from app.services.wiki_engine import withdraw_from_wiki
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.health import HealthArea, HealthEntry, HealthMeasurement, HealthTopic
from app.problems import HEALTH_BILL_NEXT_STEP, Problem, file_notices, is_health_bill_refusal, record_problem
from app.services.ingestion import (
    Classification, IncomingFile, file_for_later, finalize_document, mark_needs_attention, update_document_fields,
)
from app.templating import templates

router = APIRouter(prefix="/health", tags=["health"])
_NON_FIELD_KEYS = {"file", "category"}


def spec_for_health():
    return get_spec(Domain.HEALTH)


def _document_or_404(session: Session, document_id: int) -> Document:
    document = session.get(Document, document_id)
    if document is None or document.domain != Domain.HEALTH:
        raise HTTPException(status_code=404, detail={
            "what": "Paper not found.", "why": "No Health paper has that number; it may have been deleted, or it belongs to another tab.",
            "next_step": "Open the Health records list and choose the paper from there."})
    return document


def _upload_context(errors: dict) -> dict:
    return {"errors": file_notices(errors)}


def _measurement_view(rows: list[HealthMeasurement]) -> list[dict]:
    """The paper's results in the paper's own order and grouping: before a result, the headings (outermost first)
    that differ from the previous result's; after its value, the earlier results the paper printed for it."""
    earlier: dict[int, list[HealthMeasurement]] = {}
    for m in rows:
        if m.is_previous:
            earlier.setdefault(m.position, []).append(m)
    view: list[dict] = []
    last: list[str] = []
    for m in rows:
        if m.is_previous:
            continue
        path = [h for h in m.group_path.split(PATH_SEP) if h] if m.group_path else []
        common = 0
        while common < len(path) and common < len(last) and path[common] == last[common]:
            common += 1
        flag = None
        if m.out_of_range and m.value is not None:
            flag = "high" if (m.ref_high is not None and m.value > m.ref_high) else "low"
        view.append({"m": m, "flag": flag, "headings": [(depth, path[depth]) for depth in range(common, len(path))],
                     "earlier": earlier.get(m.position, []),
                     "reference": reference_for_display(m.reference_text, m.ref_low, m.ref_high)})
        last = path
    return view


def _heading_topics(session: Session, row: dict, cache: dict[int, Optional[HealthTopic]]) -> list[Optional[int]]:
    """For each heading shown above a result, the topic it links to (None: no link). The result's own (last) heading
    links to the result's topic, even after a merge. A heading above it links to the result's topic's ancestor at that
    level only when that ancestor still carries the printed name: after a merge the ancestors are not the headings
    printed on this paper. A wrapper heading such as "Conclusões" is not a topic."""
    m = row["m"]
    path = [h for h in m.group_path.split(PATH_SEP) if h] if m.group_path else []
    if m.topic_id is None:
        return [None for _ in row["headings"]]
    chain: list[Optional[HealthTopic]] = []
    topic_id, seen = m.topic_id, set()
    while topic_id is not None and topic_id not in seen and len(chain) < len(topics_svc.topic_path(m.group_path)):
        seen.add(topic_id)
        if topic_id not in cache:
            cache[topic_id] = session.get(HealthTopic, topic_id)
        topic = cache[topic_id]
        chain.append(topic)
        topic_id = topic.parent_id if topic is not None else None
    ids: list[Optional[int]] = []
    for depth, name in row["headings"]:
        back = len(path) - 1 - depth                    # 0 = the result's own (last) heading
        topic = chain[back] if back < len(chain) else None
        if topic is not None and (back == 0 or match_key(name) == topic.match_key):
            ids.append(topic.id)
        else:
            ids.append(None)
    return ids


def _outline_view(session: Session, entry: HealthEntry, view: list[dict]) -> list[dict]:
    """A written report's outline: its headings in the paper's order (each with the topic it names) and the prose
    under them. Numbers are not here: they go to the Metrics table."""
    cache: dict[int, Optional[HealthTopic]] = {}
    items: list[dict] = []
    for row in view:
        ids = _heading_topics(session, row, cache)
        for (depth, name), topic_id in zip(row["headings"], ids):
            items.append({"kind": "heading", "depth": depth, "name": name, "topic_id": topic_id})
        m = row["m"]
        if m.value is None and m.value_text:
            label = None if match_key(m.test) in topics_svc.DOCUMENT_SECTIONS else m.test
            items.append({"kind": "prose", "m": m, "label": label})
    return items


def _detail_context(session: Session, document: Document, values: dict, errors: dict) -> dict:
    spec = get_spec(Domain.HEALTH)
    fields = load_fields(document)
    entry = store.entry_for(session, document.id)
    measurements = _measurement_view(list(session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)
                                          .order_by(HealthMeasurement.position, HealthMeasurement.is_previous,
                                                    HealthMeasurement.measured_on)))) if entry else []
    tags = areas_svc.tags_of(session, entry.id) if entry else []
    is_report = bool(entry and entry.layout == "report")
    media_kind = media_kind_for(document.filename)
    legacy_bill = document.category in LEGACY_CATEGORIES        # filed as a bill before bills left Health: it still opens
    description = describe_document(document)
    if legacy_bill:
        description.category_label = LEGACY_LABELS[document.category]
    return {"document": document, "description": description, "legacy_bill": legacy_bill,
            "bill_refused": document.status == DocumentStatus.NEEDS_ATTENTION and is_health_bill_refusal(document.failure_reason), "media_kind": media_kind.value,
            "entry": entry, "measurements": measurements, "is_report": is_report, "path_sep": PATH_SEP,
            "outline": _outline_view(session, entry, measurements) if is_report else [],
            "metrics": [row for row in measurements if row["m"].value is not None] if is_report else [],
            "check_notes": store.notes_of(entry) if entry else [],
            "area_names": ", ".join(t.name for t in tags if t.kind == "area"),
            "condition_names": ", ".join(t.name for t in tags if t.kind == "condition"),
            "why": {k: fields.get(k, "") for k in ("indication", "clinical_notes", "requested_by", "performed_by", "details")},
            "form_fields": build_form_fields(session, spec, document.category, values, errors, media_kind=media_kind)}


async def _read_in_background(session_factory, document_id: int) -> None:
    """Read and file one paper. Runs after the response, in its own session: the reading can take minutes."""
    with session_factory() as session:
        document = session.get(Document, document_id)
        if document is None or document.status == DocumentStatus.PROCESSED:
            return
        try:
            await finalize_document(session, document, Classification(Domain.HEALTH, document.category, load_fields(document)))
        except BaseException as exc:      # also a cancelled task: the paper must never be left looking unread
            session.rollback()
            session.refresh(document)
            mark_needs_attention(session, document, f"Health processing failed: {type(exc).__name__} {exc}".strip())
            if not isinstance(exc, Exception):
                raise


@router.get("/records")
async def landing(request: Request, session: Session = Depends(get_session)):
    f = browse.parse_filters(request.query_params)
    waiting = list(session.exec(select(Document).where(
        Document.domain == Domain.HEALTH,
        Document.status.in_([DocumentStatus.PENDING, DocumentStatus.NEEDS_ATTENTION])).order_by(Document.id)))
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
                                Classification(domain=Domain.HEALTH, category=category, fields=values))
    except InvalidClassification as exc:
        return templates.TemplateResponse(request, "health/upload.html", _upload_context(exc.errors), status_code=400)
    if not result.duplicate:       # the reading takes minutes: the person lands on the paper's page, which shows its progress
        background_tasks.add_task(_read_in_background, session_factory, result.document.id)
    return RedirectResponse(document_url(result.document) or "/health/records", status_code=303)


async def prepare_reread(session: Session, document: Document) -> None:
    """The steps behind "Read again": take back everything read from a filed paper (calendar entry, wiki claims,
    results, tags), clear its fields so they start again from the new reading, and mark it pending."""
    if document.category in LEGACY_CATEGORIES:       # checked first: nothing may be withdrawn from a paper that cannot be read
        raise HTTPException(status_code=409, detail={
            "what": "Not read again.", "why": "Health no longer reads bills, so there is nothing to read it with.",
            "next_step": HEALTH_BILL_NEXT_STEP})
    if document.status == DocumentStatus.PROCESSED:
        await spec_for_health().handler.withdraw(session, document)
        await withdraw_from_wiki(session, document, description=f"{document.filename} read again")
        document.fields_json = "{}"
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
    return RedirectResponse(f"/health/documents/{document.id}", status_code=303)


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


def _topic_rows(session: Session) -> list[dict]:
    """Every topic still in the baseline as a tree in reading order (roots first, then each one's children), with its
    depth, its full path, what it "looks like", and the topics it may be merged into (not itself or what is inside it)."""
    live = list(session.exec(select(HealthTopic).where(HealthTopic.merged_into_id.is_(None))))
    by_id = {t.id: t for t in live}
    children: dict[Optional[int], list[HealthTopic]] = {}
    for t in live:
        children.setdefault(t.parent_id if t.parent_id in by_id else None, []).append(t)
    for kids in children.values():
        kids.sort(key=lambda t: (t.match_key, t.id))
    rows: list[dict] = []

    def walk(parent_id: Optional[int], depth: int, path: str) -> None:
        for t in children.get(parent_id, []):
            here = f"{path}{PATH_SEP}{t.name}" if path else t.name
            start = len(rows)
            rows.append({"t": t, "depth": depth, "path": here, "inside": set()})
            walk(t.id, depth + 1, here)
            rows[start]["inside"] = {r["t"].id for r in rows[start + 1:]}

    walk(None, 0, "")
    paths = {r["t"].id: r["path"] for r in rows}
    for r in rows:
        similar = by_id.get(r["t"].similar_to_id) if r["t"].similar_to_id is not None else None
        r["similar"] = ({"id": similar.id, "name": similar.name, "path": paths[similar.id]}
                        if similar is not None and similar.id != r["t"].id and similar.id not in r["inside"] else None)
        r["targets"] = [{"id": o["t"].id, "path": o["path"]} for o in rows
                        if o["t"].id != r["t"].id and o["t"].id not in r["inside"]]
    return rows


def _topics_page(request: Request, session: Session, error: Optional[Problem] = None, status_code: int = 200):
    return templates.TemplateResponse(request, "health/topics.html", {"topics": _topic_rows(session), "error": error},
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


def _is_prose(m: HealthMeasurement) -> bool:
    return m.value is None and bool(m.value_text)


def _conclusions(session: Session, topic: HealthTopic, entries: list[dict]) -> list[dict]:
    """The thread's papers (already newest first) with their prose grouped under the sub-topic it belongs to."""
    cache: dict[int, Optional[HealthTopic]] = {}

    def below(topic_id: Optional[int]) -> str:
        return topics_svc.path_below(session, topic_id, topic.id, cache)

    out = []
    for e in entries:
        prose = [{"sub": below(m.topic_id), "m": m} for m in e["items"] if _is_prose(m)]
        if prose:
            out.append({"entry": e["entry"], "document": e["document"], "prose": prose})
    return out


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
        "conclusions": _conclusions(session, found["topic"], found["entries"]), "metrics": found["metrics"]})


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
