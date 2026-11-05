"""Answers to the Hub's "same person?" questions (work-list B4), asked on the Inbox page."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from markupsafe import escape
from sqlmodel import Session

from app.db import get_session
from app.services import subjects as svc
from app.templating import templates

router = APIRouter(prefix="/subjects", tags=["subjects"])


def section_html(session: Session) -> str:
    return templates.get_template("inbox/_same_person.html").render(proposals=svc.pending(session))


@router.post("/matches/{match_id}")
async def answer(request: Request, match_id: int, session: Session = Depends(get_session)):
    form = await request.form()
    same = form.get("same") == "yes"
    try:
        svc.decide(session, match_id, same, getattr(request.state, "user_email", None))
    except svc.SubjectError as refused:
        p = refused.problem
        raise HTTPException(status_code=409, detail={"what": p.what, "why": p.why, "next_step": p.next_step})
    text = "Joined: one person from now on." if same else "Kept apart: the Hub won't ask again."
    return HTMLResponse(f'<div class="inbox-card"><p>{escape(text)}</p></div>')
