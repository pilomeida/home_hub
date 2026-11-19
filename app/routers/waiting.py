"""Waiting for you: /waiting (PLAN step 3). One list of everything that needs a person, filtered by area. Every
press answers with the item's new state (an HTMX fragment); a refusal comes back in the card as a three-part
message, never silently."""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from app.db import get_session
from app.problems import Problem, render_html
from app.services import review
from app.templating import templates

router = APIRouter(prefix="/waiting", tags=["waiting"])


@router.get("")
async def waiting_page(request: Request, area: Optional[str] = None, session: Session = Depends(get_session)):
    area = area if area in review.AREAS else None
    items = review.waiting(session, area)
    return templates.TemplateResponse(request, "waiting/page.html", {
        "cards": [review.card(i) for i in items], "area": area, "areas": review.AREAS,
        "counts": review.area_counts(session)})


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
