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
async def waiting_page(request: Request, area: Optional[str] = None, page: int = 1,
                       session: Session = Depends(get_session)):
    only_merchants = area == review.MERCHANTS          # the unconfirmed merchants are their own line (step 10c)
    area = area if area in review.AREAS or only_merchants else None
    page = max(1, page)
    items = review.waiting(session, None if only_merchants else area, offset=(page - 1) * review.PAGE,
                           limit=review.PAGE, merchants=only_merchants)
    counts = review.area_counts(session, merchants=False)
    merchants = review.merchant_count(session)
    total = merchants if only_merchants else counts.get(area, 0) if area else sum(counts.values())
    return templates.TemplateResponse(request, "waiting/page.html", {
        "cards": [review.card(i) for i in items], "area": area, "areas": review.AREAS, "counts": counts,
        "merchants": merchants, "merchants_key": review.MERCHANTS,
        "page": page, "pages": max(1, -(-total // review.PAGE))})


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
