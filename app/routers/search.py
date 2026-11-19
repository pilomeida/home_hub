"""Search over papers: /search (PLAN step 1d). The box sits at the top of the menu on every page; results show
each paper, where it is filed, its privacy level and the line that matched."""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from markupsafe import Markup, escape
from sqlmodel import Session

from app.categories.registry import document_url, get_spec, is_implemented
from app.db import get_session
from app.privacy.settings import configured_tier
from app.services.paper_search import query_words, search_papers
from app.templating import templates

router = APIRouter(tags=["search"])

LEVEL_WORDS = {"house_low": "Low", "house_mid": "Mid", "house_high": "High"}


def _marked(snippet: str) -> Markup:
    """The matched line, escaped, with the matched words highlighted."""
    return Markup(str(escape(snippet)).replace("\x02", "<mark>").replace("\x03", "</mark>"))


@router.get("/search")
async def search(request: Request, q: Optional[str] = None, session: Session = Depends(get_session)):
    query = (q or "").strip()
    results = []
    for hit in search_papers(session, query, limit=100) if query else []:
        doc = hit.document
        spec = get_spec(doc.category) if is_implemented(doc.category) else None
        tier = doc.sensitivity_tier or configured_tier(session, doc.category, doc.paper_kind)
        results.append({
            "document": doc, "url": document_url(doc),
            "where": f"{spec.label} › {spec.kind_label(doc.paper_kind)}" if spec else "",
            "level": LEVEL_WORDS.get(tier or "", "no level yet"),
            "line": _marked(hit.snippet) if hit.snippet else None, "words": hit.words,
        })
    return templates.TemplateResponse(request, "search/results.html", {
        "q": query, "results": results, "n_words": len(query_words(query)),
    })
