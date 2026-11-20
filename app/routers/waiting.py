"""Waiting for you: /waiting (PLAN step 3). One list of everything that needs a person, filtered by area. Every
press answers with the item's new state (an HTMX fragment); a refusal comes back in the card as a three-part
message, never silently."""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from app.db import get_session
from app.problems import Problem, render_html
from app.categories.fields import file_url, media_kind_for
from app.services import queue_view, review
from app.templating import templates

router = APIRouter(prefix="/waiting", tags=["waiting"])


@router.get("")
async def waiting_page(request: Request, area: Optional[str] = None, page: int = 1,
                       session: Session = Depends(get_session)):
    only_merchants = area == review.MERCHANTS          # the unconfirmed merchants are their own line (step 10c)
    area = area if area in review.AREAS or only_merchants else None
    page = max(1, page)
    from app.services import who
    person = None if only_merchants else who.chosen(session, request.cookies.get(who.COOKIE))
    if person is not None:                     # the Who chip: items about one of this person's papers
        mine = who.document_ids(session, person.id)
        every = [i for i in review.waiting(session, area, limit=None, merchants=False)
                 if (d := queue_view.document_of(session, i)) is not None and d.id in mine]
        total = len(every)
        items = every[(page - 1) * review.PAGE:page * review.PAGE]
    else:
        items = review.waiting(session, None if only_merchants else area, offset=(page - 1) * review.PAGE,
                               limit=review.PAGE, merchants=only_merchants)
    counts = review.area_counts(session, merchants=False)       # after `waiting`, which brings the queue up to date
    merchants = review.merchant_count(session)
    if person is None:
        total = merchants if only_merchants else counts.get(area, 0) if area else sum(counts.values())
    return templates.TemplateResponse(request, "waiting/page.html", {
        "cards": [] if only_merchants else [review.card(i) for i in items],
        "rows": queue_view.merchant_rows(session, items) if only_merchants else [],
        "groups": [] if area or person else queue_view.groups(session), "person": person,
        "area": area, "areas": review.AREAS, "counts": counts,
        "merchants": merchants, "merchants_key": review.MERCHANTS,
        "page": page, "pages": max(1, -(-total // review.PAGE))})


class _Move(Exception):
    """Next / previous: no answer to record."""


def _one_context(session: Session, area: Optional[str], n: int, problem: Optional[str] = None) -> dict:
    area = area if area in review.AREAS or area == review.MERCHANTS else None
    shown = queue_view.one(session, area, n)
    return {"one": shown, "area": area, "areas": review.AREAS, "problem": problem,
            "file": file_url(shown.document) if shown and shown.document else None,
            "media": media_kind_for(shown.document.filename).value if shown and shown.document else None}


@router.get("/one")
async def one_page(request: Request, area: Optional[str] = None, n: int = 0, session: Session = Depends(get_session)):
    """One item at a time (step 10e): the paper on the left, what the Hub read on the right, keyboard shortcuts."""
    review.sync(session)
    name = "waiting/_one.html" if request.headers.get("hx-request") else "waiting/one.html"
    return templates.TemplateResponse(request, name, _one_context(session, area, max(0, n)))


@router.post("/one/{item_id}")
async def one_answer(request: Request, item_id: int, area: Optional[str] = None, n: int = 0,
                     session: Session = Depends(get_session)):
    """Answer the item shown, then show the next one (the answered item leaves the list, so the same position)."""
    form = await request.form()
    answer = str(form.get("answer") or "")
    problem = None
    try:
        if answer in ("next", "previous"):          # moving along answers nothing
            raise _Move
        review.decide(session, item_id, answer, by=getattr(request.state, "user_email", None),
                      remember=form.get("remember") in ("1", "on", "true"))
        session.commit()
    except _Move:
        pass
    except review.ReviewError as exc:
        session.rollback()
        problem = render_html(Problem(exc.what, exc.why, exc.next_step))
    step = 1 if answer == "next" else -1 if answer == "previous" else 0
    return templates.TemplateResponse(request, "waiting/_one.html", _one_context(session, area, max(0, n + step), problem))


@router.post("/bulk")
async def bulk_confirm(request: Request, session: Session = Depends(get_session)):
    """Confirm many merchants in one go (step 10e): each is answered as on its own card, "Yes, that's right"."""
    form = await request.form()
    ids = [int(v) for v in form.getlist("item") if str(v).isdigit()]
    by = getattr(request.state, "user_email", None)
    done, refused = 0, []
    for item_id in ids:
        try:
            review.decide(session, item_id, review.APPROVE, by=by, remember=form.get("remember") in ("1", "on", "true"))
            session.commit()
            done += 1
        except review.ReviewError as exc:
            session.rollback()
            refused.append(exc)
    items = review.waiting(session, merchants=True, limit=review.PAGE)
    note = None
    if not ids:
        note = render_html(Problem("Nothing was confirmed.", "No merchant was ticked.",
                                   "Tick the merchants whose guess is right, then press Confirm."))
    elif refused:
        first = refused[0]
        note = render_html(Problem(f"{len(refused)} of {len(ids)} not confirmed{f' ({done} were)' if done else ''}.",
                                   first.why, first.next_step))
    return templates.TemplateResponse(request, "waiting/_merchants.html", {
        "rows": queue_view.merchant_rows(session, items), "done": done, "note": note,
        "merchants": review.merchant_count(session)})


@router.post("/{item_id}")
async def answer(request: Request, item_id: int, session: Session = Depends(get_session)):
    form = await request.form()
    by = getattr(request.state, "user_email", None)
    try:
        item = review.decide(session, item_id, str(form.get("answer") or ""), by=by,
                             remember=form.get("remember") in ("1", "on", "true"))
    except review.ReviewError as exc:
        problem = render_html(Problem(exc.what, exc.why, exc.next_step))
        return HTMLResponse(f'<div class="problem" id="review-{item_id}">{problem}</div>', status_code=409)
    return templates.TemplateResponse(request, "waiting/_done.html", {"item": item})
