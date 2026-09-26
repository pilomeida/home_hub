"""Routes for browsing the knowledge layer: generated index, pages (current
claims with their source documents, superseded claims), and the log."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from app.db import get_session
from app.domains.fields import file_url
from app.domains.registry import document_url
from app.models.wiki import WikiChange, WikiPage
from app.routers.wiki_lint import open_finding_count
from app.services.presentation import humanize_key
from app.services.wiki_store import (
    active_claims, build_wiki_index, links_from, links_to, recent_log, sources_for_claims, superseded_claims,
)
from app.templating import templates

router = APIRouter(prefix="/wiki", tags=["wiki"])

_RECENTLY_CHANGED_DAYS = 7
# The legacy WikiChange fact_key meaning "the whole fact set was recorded at
# once" (pre-claims ingestion) -- new_value is a JSON object, not a single value.
_WHOLE_FACT_SET_KEY = "*"


@dataclass
class SourceLink:
    filename: str
    url: str


@dataclass
class LegacyChangeView:
    """One row of the legacy (pre-claims) WikiChange history, ready to
    render: either a single fact_key/old/new change, or -- when fact_key is
    "*" -- the whole fact set recorded at once, as a readable list."""

    changed_at: datetime
    is_initial: bool
    label: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    facts: list[tuple[str, str]] = field(default_factory=list)


def _legacy_change_views(changes: list[WikiChange]) -> list[LegacyChangeView]:
    views = []
    for change in changes:
        if change.fact_key == _WHOLE_FACT_SET_KEY:
            facts = json.loads(change.new_value)
            views.append(LegacyChangeView(
                changed_at=change.changed_at, is_initial=True, label="Initial facts recorded",
                facts=[(humanize_key(key), value) for key, value in facts.items()],
            ))
        else:
            views.append(LegacyChangeView(
                changed_at=change.changed_at, is_initial=False, label=humanize_key(change.fact_key),
                old_value=change.old_value, new_value=change.new_value,
            ))
    return views


@router.get("")
async def wiki_index(request: Request, session: Session = Depends(get_session)):
    cutoff = datetime.utcnow() - timedelta(days=_RECENTLY_CHANGED_DAYS)
    sections = build_wiki_index(session)
    recently_changed_ids = {e.page_id for s in sections for e in s.entries if e.updated_at >= cutoff}
    return templates.TemplateResponse(
        request, "wiki/list.html", {"sections": sections, "recently_changed_ids": recently_changed_ids,
                                    "lint_open_count": open_finding_count(session)}
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
        "history": history, "sources": sources, "changes": _legacy_change_views(legacy_changes),
        "linked_pages": sorted({p.id: p for p in links_from(session, page_id) + links_to(session, page_id)}.values(),
                               key=lambda p: p.topic.lower()),
    })
