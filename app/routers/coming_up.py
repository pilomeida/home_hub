"""Coming up (work-list B3b): what is due in the next three months, and the commitments a person sets up."""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.db import get_session
from app.models.category_node import CategoryNode
from app.models.commitment import Cadence, Commitment
from app.services import coming_up as svc
from app.services.taxonomy import node_options, path_label
from app.templating import templates

router = APIRouter(prefix="/financials/coming-up", tags=["coming-up"])

CADENCES = [(Cadence.MONTHLY.value, "Every month"), (Cadence.QUARTERLY.value, "Every 3 months"),
            (Cadence.YEARLY.value, "Every year"), (Cadence.IRREGULAR.value, "Once (on that date)")]


def _page(request: Request, session: Session, *, values: dict, problem=None, status: int = 200):
    commitments = session.exec(select(Commitment).order_by(Commitment.name)).all()
    places = {c.id: path_label(session, session.get(CategoryNode, c.category_id)) if c.category_id else None
              for c in commitments}
    leaves = [o for o in node_options(session) if o["level"] == 3 and o["kind"] == "out"]
    return templates.TemplateResponse(request, "coming_up/page.html", {
        "view": svc.coming_up(session, date.today()), "commitments": commitments, "places": places,
        "leaves": leaves, "cadences": CADENCES, "values": values, "problem": problem}, status_code=status)


@router.get("")
async def coming_up_page(request: Request, from_line: Optional[int] = None, session: Session = Depends(get_session)):
    return _page(request, session, values=svc.prefill_from_line(session, from_line))


@router.post("/commitments")
async def add_commitment(request: Request, session: Session = Depends(get_session)):
    form = dict(await request.form())
    try:
        svc.create_commitment(session, name=form.get("name", ""), amount=form.get("amount", ""),
                              cadence=form.get("cadence", ""), next_due=form.get("next_due", ""),
                              category_id=form.get("category_id"))
    except svc.CommitmentRefused as refused:
        return _page(request, session, values=form, problem=refused, status=400)
    return RedirectResponse("/financials/coming-up", status_code=303)


@router.post("/commitments/{commitment_id}/remove")
async def remove_commitment(commitment_id: int, session: Session = Depends(get_session)):
    try:
        svc.remove_commitment(session, commitment_id)
    except svc.CommitmentRefused as refused:
        raise HTTPException(status_code=409, detail={"what": refused.what, "why": refused.why,
                                                     "next_step": refused.next_step, "link": "/financials/coming-up"})
    return RedirectResponse("/financials/coming-up", status_code=303)
