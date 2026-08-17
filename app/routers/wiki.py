"""Routes for browsing the auto-maintained wiki."""

import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.models.wiki import WikiChange, WikiPage

router = APIRouter(prefix="/wiki", tags=["wiki"])
templates = Jinja2Templates(directory="app/templates")

_RECENTLY_CHANGED_DAYS = 7


@router.get("")
async def list_wiki_pages(request: Request, session: Session = Depends(get_session)):
    pages = session.exec(select(WikiPage).order_by(WikiPage.topic)).all()
    cutoff = datetime.utcnow() - timedelta(days=_RECENTLY_CHANGED_DAYS)
    recently_changed_ids = {p.id for p in pages if p.updated_at >= cutoff}
    return templates.TemplateResponse(
        request, "wiki/list.html", {"pages": pages, "recently_changed_ids": recently_changed_ids}
    )


@router.get("/{page_id}")
async def wiki_page_detail(request: Request, page_id: int, session: Session = Depends(get_session)):
    page = session.get(WikiPage, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Wiki page not found")
    changes = session.exec(
        select(WikiChange).where(WikiChange.wiki_page_id == page_id).order_by(WikiChange.changed_at.desc())
    ).all()
    return templates.TemplateResponse(
        request, "wiki/page.html", {"page": page, "facts": json.loads(page.facts_json), "changes": changes}
    )
