"""The Problems page: what the Hub was asked to do and could not, with the reason and the next step (D14)."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.db import get_session
from app.models.schedule import ProblemEntry
from app.problems import open_problems
from app.templating import templates

router = APIRouter(prefix="/problems", tags=["problems"])


@router.get("")
def page(request: Request, session: Session = Depends(get_session)):
    recent_resolved = session.exec(select(ProblemEntry).where(ProblemEntry.resolved_at != None)  # noqa: E711
                                   .order_by(ProblemEntry.resolved_at.desc()).limit(10)).all()
    return templates.TemplateResponse(request, "problems/page.html", {
        "open": open_problems(session), "resolved": recent_resolved})


@router.post("/{problem_id}/dismiss")
def dismiss(request: Request, problem_id: int, session: Session = Depends(get_session)):
    entry = session.get(ProblemEntry, problem_id)
    if entry is not None and entry.resolved_at is None:
        entry.resolved_at = datetime.utcnow()
        entry.resolved_by = getattr(request.state, "user_email", None) or "dismissed"
        session.add(entry); session.commit()
    return RedirectResponse("/problems", status_code=303)
