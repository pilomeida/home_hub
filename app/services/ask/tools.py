"""Core Ask tools (wiki, sources, todos) + collection of domain tools
declared on the registry. All read-only. The ONLY place Ask/Lint decide
which sources are visible: settled_entries() / is_settled() /
is_settled_source(). Claim sources are read only via claim_sources(),
which excludes withdrawn links."""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from app.domains.base import SourceKind
from app.domains.entries import SourceEntry, domain_entries
from app.domains.fields import effective_fields
from app.domains.registry import get_spec, implemented_domains
from app.models.document import Document, DocumentStatus
from app.models.domain import Domain
from app.models.record import Record
from app.models.todo import Todo
from app.models.wiki import WikiPage
from app.services.ask.contracts import AskTool, ToolOutput
from app.services.ask.refs import (
    Source, citable_for_source, citable_for_todo, citable_for_wiki_page, source_ref,
)
from app.services.document_input import UnsupportedFileTypeError, build_content_block
from app.services.wiki_store import active_claims, claim_sources, links_from, links_to, superseded_claims

logger = logging.getLogger(__name__)

READ_FILE_TOOL = "read_document_file"
_MAX_FILE_BYTES = 10 * 1024 * 1024
_MAX_PAGES_PER_CALL = 10


def fold(text: str) -> str:
    """Lowercase, accent-free: 'Caldéira' -> 'caldeira' (Portuguese records)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"\w+", fold(text)) if len(t) >= 2}


def is_settled(doc: Optional[Document]) -> bool:
    return doc is not None and doc.domain is not None and doc.status == DocumentStatus.PROCESSED


def is_settled_source(source: Optional[Source]) -> bool:
    if isinstance(source, Record):
        return source.retired_at is None
    return is_settled(source)


def settled_entries(session: Session, domain: Optional[Domain] = None) -> list[SourceEntry]:
    """PROCESSED documents and non-retired records of implemented domains, newest first."""
    domains = [domain] if domain is not None else [s.domain for s in implemented_domains()]
    entries = [e for d in domains for e in domain_entries(session, d)
               if e.kind == SourceKind.RECORD or is_settled(e.document)]
    return sorted(entries, key=lambda e: e.created_at, reverse=True)


def _entry_source(entry: SourceEntry) -> Source:
    return entry.record if entry.kind == SourceKind.RECORD else entry.document


def entry_fields_text(entry: SourceEntry) -> str:
    spec = get_spec(_entry_source(entry).domain)
    fields = effective_fields(spec, entry.category, entry.fields)
    labels = {f.key: f.label for f in spec.fields}
    return "; ".join(f"{labels.get(k, k.replace('_', ' '))}: {v}" for k, v in fields.items() if v not in (None, ""))


def _parse_domain(value) -> Optional[Domain]:
    return Domain(value) if value else None  # ValueError -> tool error via engine


def _claim_line(claim, sources: list[Source]) -> str:
    refs = ", ".join(source_ref(s) for s in sources) or "none"
    note = f" — NOTE: {claim.note}" if claim.note else ""
    return f"- {claim.label or claim.key} ({claim.key}): {claim.value}{note} (recorded {claim.created_at:%Y-%m-%d}; sources: {refs})"


def _read_wiki_pages(session: Session, args: dict) -> ToolOutput:
    page_ids = [int(i) for i in args.get("page_ids", [])][:_MAX_PAGES_PER_CALL]
    if not page_ids:
        return ToolOutput(text="No page_ids given.", is_error=True)
    parts, citables = [], []
    for page_id in page_ids:
        page = session.get(WikiPage, page_id)
        if page is None:
            parts.append(f"wiki:{page_id}: not found")
            continue
        citables.append(citable_for_wiki_page(page.id, page.topic))
        current, history = active_claims(session, page.id), superseded_claims(session, page.id)
        sources = claim_sources(session, [c.id for c in current + history])   # withdrawn links excluded
        for source in {source_ref(s): s for ss in sources.values() for s in ss}.values():
            if is_settled_source(source):
                citables.append(citable_for_source(source))
        lines = [f"## wiki:{page.id} — {page.topic} ({page.domain.value if page.domain else 'general'}, {page.page_type})",
                 f"Summary: {page.summary or '-'}", "Current facts:"]
        lines += [_claim_line(c, sources.get(c.id, [])) for c in current] or ["- (none)"]
        if history:
            lines.append("Superseded facts (history only, NOT current):")
            lines += [f"- {c.key}: {c.value} (superseded {c.superseded_at:%Y-%m-%d})" for c in history]
        neighbours = {p.id: p for p in links_from(session, page.id) + links_to(session, page.id)}
        if neighbours:
            lines.append("Linked pages: " + "; ".join(f"wiki:{p.id} {p.topic}" for p in neighbours.values()))
            citables += [citable_for_wiki_page(p.id, p.topic) for p in neighbours.values()]
        parts.append("\n".join(lines))
    return ToolOutput(text="\n\n".join(parts), citables=citables)


def _find_sources(session: Session, args: dict) -> ToolOutput:
    domain = _parse_domain(args.get("domain"))
    category = args.get("category")
    limit = max(1, min(int(args.get("limit", 30)), 60))
    query_tokens = tokens(args.get("query") or "")
    scored = []
    for entry in settled_entries(session, domain):
        if category and entry.category != category:
            continue
        source = _entry_source(entry)
        spec = get_spec(source.domain)
        fields_text = entry_fields_text(entry)
        haystack = " ".join([entry.title, entry.category or "", spec.category_label(entry.category), spec.label, fields_text])
        score = len(query_tokens & tokens(haystack)) if query_tokens else 1
        if score > 0:
            scored.append((score, entry, source, spec, fields_text))
    scored.sort(key=lambda item: (-item[0], -item[1].created_at.timestamp()))
    shown = scored[:limit]
    lines = [f"{len(scored)} matching filed sources (showing {len(shown)}):"]
    for _, entry, source, spec, fields_text in shown:
        attachment = (f" | attachment doc:{entry.document.id}"
                      if entry.kind == SourceKind.RECORD and entry.document is not None else "")
        lines.append(f"{source_ref(source)} | {spec.label} | {spec.category_label(entry.category) or '-'} | "
                     f"{entry.title} | added {entry.created_at:%Y-%m-%d} | {fields_text or 'no details'}{attachment}")
    return ToolOutput(text="\n".join(lines), citables=[citable_for_source(s) for _, _, s, _, _ in shown],
                      used_raw_sources=True)


def _read_document_file(session: Session, args: dict) -> ToolOutput:
    doc = session.get(Document, int(args["document_id"]))
    if not is_settled(doc):
        return ToolOutput(text="That document is not available (not found, or not filed yet).", is_error=True)
    path = Path(doc.file_path)
    if not path.exists() or path.stat().st_size > _MAX_FILE_BYTES:
        return ToolOutput(text=f"doc:{doc.id} file is missing or too large to read; use its details instead.", is_error=True)
    try:
        block = build_content_block(str(path))
    except UnsupportedFileTypeError:
        return ToolOutput(text=f"doc:{doc.id} can't be read (e.g. a video); use its details instead.", is_error=True)
    return ToolOutput(text=f"Contents of doc:{doc.id} ({doc.filename}) attached below.",
                      citables=[citable_for_source(doc)], content_blocks=[block], used_raw_sources=True)


def _list_todos(session: Session, args: dict) -> ToolOutput:
    domain = _parse_domain(args.get("domain"))
    statement = select(Todo)
    if not args.get("include_done", False):
        statement = statement.where(Todo.done == False)  # noqa: E712
    if domain is not None:
        statement = statement.where(Todo.domain == domain)
    todos = session.exec(statement.order_by(Todo.due_date).limit(100)).all()
    lines = [f"{len(todos)} to-dos:"] + [
        f"todo:{t.id} | {t.title} | due {t.due_date or '-'} | {'done' if t.done else 'open'} | "
        f"{t.domain.value if t.domain else 'general'}" for t in todos]
    return ToolOutput(text="\n".join(lines), citables=[citable_for_todo(t) for t in todos], used_raw_sources=True)


def core_tools() -> list[AskTool]:
    domain_prop = {"type": "string", "enum": [s.domain.value for s in implemented_domains()],
                   "description": "Restrict to one domain."}
    return [
        AskTool(name="read_wiki_pages",
                description="Read wiki pages: current facts (with any NOTE, e.g. an assumption) and their sources, "
                            "superseded history, linked pages. Use this FIRST, choosing pages from the wiki index.",
                input_schema={"type": "object", "properties": {
                    "page_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": _MAX_PAGES_PER_CALL}},
                    "required": ["page_ids"]},
                run=_read_wiki_pages),
        AskTool(name="find_sources",
                description="Search filed documents AND hand-entered records (e.g. a logged maintenance visit) by "
                            "keywords over title, category and details (item, room, dates...). Omit query to list by "
                            "recency. Use only when the wiki doesn't answer.",
                input_schema={"type": "object", "properties": {
                    "query": {"type": "string"}, "domain": domain_prop,
                    "category": {"type": "string", "description": "A category value from the domain list."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 60}}},
                run=_find_sources),
        AskTool(name=READ_FILE_TOOL,
                description="Attach one document's actual file (PDF or photo), or a record's attachment, to read "
                            "inside it. Expensive: at most 2 per question, only when wiki and details are insufficient.",
                input_schema={"type": "object", "properties": {"document_id": {"type": "integer"}},
                              "required": ["document_id"]},
                run=_read_document_file),
        AskTool(name="list_todos",
                description="List household to-dos (open by default), optionally for one domain.",
                input_schema={"type": "object", "properties": {"domain": domain_prop, "include_done": {"type": "boolean"}}},
                run=_list_todos),
    ]


def domain_tools() -> list[AskTool]:
    tools: list[AskTool] = []
    for spec in implemented_domains():
        for tool in spec.ask_tools:
            if not tool.name.startswith(f"{spec.domain.value}_"):
                raise ValueError(f"Ask tool {tool.name!r} must be prefixed with '{spec.domain.value}_'")
            tools.append(tool)
    names = [t.name for t in tools]
    if len(names) != len(set(names)):
        raise ValueError(f"Duplicate Ask tool names: {names}")
    return tools


def available_tools() -> list[AskTool]:
    return core_tools() + domain_tools()
