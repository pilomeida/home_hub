"""Routes for browsing the knowledge layer: generated index, pages (current
claims with their source documents, superseded claims), and the log."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from app.db import get_session
from app.domains.fields import file_url
from app.domains.registry import document_url
from app.models.wiki import WikiChange, WikiPage
from app.services.wiki_store import (
    active_claims, build_wiki_index, links_from, links_to, recent_log, sources_for_claims, superseded_claims,
)
from app.templating import templates

router = APIRouter(prefix="/wiki", tags=["wiki"])

_RECENTLY_CHANGED_DAYS = 7


@dataclass
class SourceLink:
    filename: str
    url: str


@router.get("")
async def wiki_index(request: Request, session: Session = Depends(get_session)):
    cutoff = datetime.utcnow() - timedelta(days=_RECENTLY_CHANGED_DAYS)
    sections = build_wiki_index(session)
    recently_changed_ids = {e.page_id for s in sections for e in s.entries if e.updated_at >= cutoff}
    return templates.TemplateResponse(
        request, "wiki/list.html", {"sections": sections, "recently_changed_ids": recently_changed_ids}
    )


@router.get("/log")
async def wiki_log(request: Request, session: Session = Depends(get_session)):
    entries = recent_log(session)
    page_ids = {pid for e in entries for pid in json.loads(e.page_ids_json)}
    titles = {}
    if page_ids:
        titles = {p.id: p.topic for p in session.exec(select(WikiPage).where(WikiPage.id.in_(page_ids))).all()}
    rows = [(e, [(pid, titles.get(pid, f"#{pid}")) for pid in json.loads(e.page_ids_json)]) for e in entries]
    return templates.TemplateResponse(request, "wiki/log.html", {"rows": rows})


@router.get("/{page_id}")
async def wiki_page_detail(request: Request, page_id: int, session: Session = Depends(get_session)):
    page = session.get(WikiPage, page_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Wiki page not found")
    current = active_claims(session, page_id)
    history = superseded_claims(session, page_id)
    documents = sources_for_claims(session, [c.id for c in current + history])
    sources = {
        claim_id: [SourceLink(d.filename, document_url(d) or file_url(d)) for d in docs]
        for claim_id, docs in documents.items()
    }
    legacy_changes = session.exec(
        select(WikiChange).where(WikiChange.wiki_page_id == page_id).order_by(WikiChange.changed_at.desc())
    ).all()
    return templates.TemplateResponse(request, "wiki/page.html", {
        "page": page, "facts": json.loads(page.facts_json), "active_claims": current,
        "history": history, "sources": sources, "changes": legacy_changes,
        "linked_pages": sorted({p.id: p for p in links_from(session, page_id) + links_to(session, page_id)}.values(),
                               key=lambda p: p.topic.lower()),
    })
