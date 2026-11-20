"""Search over papers: /search (PLAN step 1d). The box sits at the top of the menu on every page; results show
each paper, where it is filed, its privacy level and the line that matched."""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from markupsafe import Markup, escape
from sqlmodel import Session, func, select

from app.categories.registry import document_url, get_spec, is_implemented
from app.db import get_session
from app.models.document import Document, DocumentStatus
from app.privacy.settings import configured_tier
from app.services.paper_search import query_words, search_papers
from app.templating import templates

router = APIRouter(tags=["search"])

LEVEL_WORDS = {"house_low": "Low", "house_mid": "Mid", "house_high": "High"}


def _marked(snippet: str) -> Markup:
    """The matched line, escaped, with the matched words highlighted."""
    return Markup(str(escape(snippet)).replace("\x02", "<mark>").replace("\x03", "</mark>"))


def _row(session: Session, doc: Document, line=None, words: int = 0) -> dict:
    spec = get_spec(doc.category) if is_implemented(doc.category) else None
    tier = doc.sensitivity_tier or configured_tier(session, doc.category, doc.paper_kind)
    return {"document": doc, "url": document_url(doc),
            "where": f"{spec.label} › {spec.kind_label(doc.paper_kind)}" if spec else "",
            "level": LEVEL_WORDS.get(tier or "", "no level yet"),
            "line": _marked(line) if line else None, "words": words}


PAPERS_PAGE = 50


@router.get("/papers")
async def papers(request: Request, q: Optional[str] = None, category: Optional[str] = None, page: int = 1,
                 session: Session = Depends(get_session)):
    """Papers (PLAN step 10f): everything filed, newest first, one chip per category, and the search. The same papers
    /search finds (filed ones only), browsed instead of searched."""
    from app.services import who
    query = (q or "").strip()
    person = who.chosen(session, request.cookies.get(who.COOKIE))
    filed = (Document.status == DocumentStatus.PROCESSED, Document.category.is_not(None))
    if person is not None:                                     # the Who chip: this person's papers only
        filed = filed + (Document.id.in_(who.document_ids(session, person.id)),)
    counts = dict(session.exec(select(Document.category, func.count()).where(*filed).group_by(Document.category)).all())
    chips = sorted(((c.value, get_spec(c).label if is_implemented(c) else c.value.title(), n) for c, n in counts.items()),
                   key=lambda c: (c[0] == "other", c[1]))
    chosen = next((c for c in counts if c.value == category), None)
    page = max(1, page)
    if query:
        mine = who.document_ids(session, person.id) if person is not None else None
        hits = [h for h in search_papers(session, query, limit=500)
                if (chosen is None or h.document.category == chosen) and (mine is None or h.document.id in mine)]
        total = len(hits)
        rows = [_row(session, h.document, h.snippet, h.words)
                for h in hits[(page - 1) * PAPERS_PAGE:page * PAPERS_PAGE]]
    else:
        where = filed + ((Document.category == chosen,) if chosen is not None else ())
        total = session.exec(select(func.count()).select_from(Document).where(*where)).one()
        docs = session.exec(select(Document).where(*where).order_by(Document.created_at.desc(), Document.id.desc())
                            .offset((page - 1) * PAPERS_PAGE).limit(PAPERS_PAGE)).all()
        rows = [_row(session, d) for d in docs]
    return templates.TemplateResponse(request, "search/papers.html", {
        "q": query, "rows": rows, "chips": chips, "category": chosen.value if chosen else None,
        "all": sum(counts.values()), "total": total, "page": page, "pages": max(1, -(-total // PAPERS_PAGE)),
        "n_words": len(query_words(query)), "person": person})


@router.get("/search")
async def search(request: Request, q: Optional[str] = None, session: Session = Depends(get_session)):
    query = (q or "").strip()
    results = []
    for hit in search_papers(session, query, limit=100) if query else []:
        results.append(_row(session, hit.document, hit.snippet, hit.words))
    return templates.TemplateResponse(request, "search/results.html", {
        "q": query, "results": results, "n_words": len(query_words(query)),
    })
