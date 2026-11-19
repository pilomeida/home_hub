"""Search over papers (PLAN step 1, slices a and b).

a. Every filed paper gets a text layer (`paper_texts`): the PDF's own text at filing (`store_pdf_text`); a paper with
   no text layer (a scan, a photo) is read by the AI at the paper's own level, a few at a time, by the "Paper text"
   job (`read_missing_texts`). Nothing here can break filing: a paper whose text could not be got is still filed.
b. One SQLite FTS5 index (`paper_search`, diacritics folded, so "caldeira" finds "Caldéira") over each filed paper's
   title, kind, details and text. `search_papers` answers both the search page and Ask's `find_sources`; given a
   binding Reach it returns only papers the AI may read (app/privacy/reach.py, decision D16).

A paper's score is how many of the searched words it contains (anywhere: title, kind, details or text), the same
measure Ask used before the text layer existed; ties go to the newest paper."""

from __future__ import annotations

import asyncio
import io
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import text as sql
from sqlmodel import Session, select

from app.models.document import Document, DocumentStatus
from app.models.paper_text import AI_READ, FAILED, MAX_ATTEMPTS, NO_TEXT, PDF_TEXT, PaperText
from app.services.names import fold

logger = logging.getLogger(__name__)

# A PDF whose text layer has fewer visible characters than this per page is a scan with a stray header or page
# number: the AI reads it instead.
_MIN_CHARS_PER_PAGE = 40
_MAX_TEXT_CHARS = 400_000            # a 48-page insurance booklet is ~250k; anything longer is cut, not refused
_MAX_AI_FILE_BYTES = 10 * 1024 * 1024
_MARK_START, _MARK_END = "\x02", "\x03"

_CREATE = ("CREATE VIRTUAL TABLE IF NOT EXISTS paper_search USING fts5("
           "title, kind, fields, body, tokenize='unicode61 remove_diacritics 2')")


# --- a. the text layer ---------------------------------------------------------------------------

def pdf_text_of(file_path: str) -> Optional[str]:
    """The text a PDF carries itself, or None when it has none worth using (not a PDF, a scan, unreadable)."""
    if not file_path.lower().endswith(".pdf"):
        return None
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(Path(file_path).read_bytes()))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception:            # a damaged or encrypted PDF has no usable text layer; the AI may still read it
        return None
    text = re.sub(r"[ \t]+", " ", "\n".join(pages))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    if len(re.sub(r"\s", "", text)) < _MIN_CHARS_PER_PAGE * max(len(pages), 1):
        return None
    return text[:_MAX_TEXT_CHARS]


def is_settled(doc: Optional[Document]) -> bool:
    return doc is not None and doc.category is not None and doc.status == DocumentStatus.PROCESSED


def text_row(session: Session, document_id: int) -> Optional[PaperText]:
    return session.exec(select(PaperText).where(PaperText.document_id == document_id)).first()


def _save_text(session: Session, document_id: int, text: str, method: str, *, error: Optional[str] = None,
               attempt: bool = False) -> PaperText:
    row = text_row(session, document_id) or PaperText(document_id=document_id, method=method)
    row.text, row.method, row.error, row.updated_at = text, method, error, datetime.utcnow()
    if attempt:
        row.attempts += 1
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def store_pdf_text(session: Session, document: Document) -> Optional[PaperText]:
    """At filing: keep the PDF's own text, when it has one. A paper without one is left to the AI read."""
    existing = text_row(session, document.id)
    if existing is not None and existing.method in (PDF_TEXT, AI_READ):
        return existing                      # a paper re-filed keeps its text: the file never changes
    text = pdf_text_of(document.file_path)
    return _save_text(session, document.id, text, PDF_TEXT) if text else existing


def after_filing(session: Session, document: Document) -> None:
    """Called by the ingestion core once a paper is filed or its details change: its text, then its index row.
    Never raises; a failure is put on the Problems page and the job tries again later."""
    try:
        if is_settled(document):
            store_pdf_text(session, document)
        index_document(session, document)
    except Exception as exc:
        session.rollback()
        from app.problems import Problem, record_problem
        record_problem(session, area="Search", ref=f"document:{document.id}:search", technical=repr(exc),
                       problem=Problem(f"{document.filename} can't be found by search yet.",
                                       "Taking its text for search failed; the paper itself was filed.",
                                       "Nothing to do: the Paper text job tries again within the hour. If this "
                                       "stays here, tell Pedro.", link="/schedules"))


# --- b. the index -----------------------------------------------------------------------------------

def ensure_index(session: Session) -> None:
    session.exec(sql(_CREATE))


def _fields_text(document: Document) -> tuple[str, str]:
    """(kind words, details words) of a filed paper, as the Hub shows them."""
    from app.categories.fields import effective_fields, load_fields
    from app.categories.registry import UnknownCategoryError, get_spec
    try:
        spec = get_spec(document.category)
    except UnknownCategoryError:
        return "", ""
    labels = {f.key: f.label for f in spec.fields}
    fields = effective_fields(spec, document.paper_kind, load_fields(document))
    details = "; ".join(f"{labels.get(k, k.replace('_', ' '))}: {v}" for k, v in fields.items() if v not in (None, ""))
    return f"{spec.label} {spec.kind_label(document.paper_kind)} {document.paper_kind or ''}", details


def index_document(session: Session, document: Document) -> None:
    """(Re)write one paper's index row; a paper not (or no longer) filed has none."""
    ensure_index(session)
    session.exec(sql("DELETE FROM paper_search WHERE rowid = :id"), params={"id": document.id})
    if is_settled(document):
        kind, details = _fields_text(document)
        row = text_row(session, document.id)
        session.exec(sql("INSERT INTO paper_search(rowid, title, kind, fields, body) VALUES (:id, :t, :k, :f, :b)"),
                     params={"id": document.id, "t": document.filename, "k": kind, "f": details,
                             "b": row.text if row is not None else ""})
    session.commit()


def rebuild_index(session: Session) -> int:
    """Every filed paper's row, from scratch (the job runs it, so a change made outside the filing path is caught)."""
    ensure_index(session)
    session.exec(sql("DELETE FROM paper_search"))
    session.commit()
    docs = session.exec(select(Document).where(Document.category.is_not(None),
                                               Document.status == DocumentStatus.PROCESSED)).all()
    for doc in docs:
        index_document(session, doc)
    return len(docs)


def query_words(query: str) -> list[str]:
    """The searched words, folded the way the index folds them; one-letter words are dropped."""
    seen: list[str] = []
    for word in re.findall(r"\w+", fold(query or "")):
        if len(word) >= 2 and word not in seen:
            seen.append(word)
    return seen[:12]


@dataclass(frozen=True)
class PaperHit:
    document: Document
    words: int                    # how many of the searched words it contains
    snippet: str                  # the matched line, matches between \x02 and \x03; "" when only the title matched

    def snippet_plain(self, quote: str = "") -> str:
        return self.snippet.replace(_MARK_START, quote).replace(_MARK_END, quote)


def _match_any(words: list[str]) -> str:
    return " OR ".join(f'"{w}"*' for w in words)


def search_papers(session: Session, query: str, *, reach=None, limit: int = 50) -> list[PaperHit]:
    """Filed papers containing any of the words, most words first, then newest. With a filtering `reach`
    (app/privacy/reach.py) a paper the AI may not read is never returned, nor its text."""
    words = query_words(query)
    if not words:
        return []
    ensure_index(session)
    counts: dict[int, int] = {}
    for word in words:
        for (doc_id,) in session.exec(sql("SELECT rowid FROM paper_search WHERE paper_search MATCH :q"),
                                      params={"q": f'"{word}"*'}):
            counts[doc_id] = counts.get(doc_id, 0) + 1
    allowed = reach.included_document_ids(session) if reach is not None else None
    docs = {d.id: d for d in session.exec(select(Document).where(Document.id.in_(list(counts)))).all()} if counts else {}
    ranked = [d for i, d in docs.items() if is_settled(d) and (allowed is None or i in allowed)]
    if reach is not None:
        ranked = [d for d in ranked if reach.includes_file_of(session, d)]   # an attachment follows its record's type too
    ranked.sort(key=lambda d: (-counts[d.id], -d.created_at.timestamp()))
    ranked = ranked[:limit]
    snippets: dict[int, str] = {}
    if ranked:
        ids = ",".join(str(d.id) for d in ranked)
        rows = session.exec(sql(
            f"SELECT rowid, snippet(paper_search, 3, :a, :b, '…', 14), snippet(paper_search, -1, :a, :b, '…', 14) "
            f"FROM paper_search WHERE paper_search MATCH :q AND rowid IN ({ids})"),
            params={"q": _match_any(words), "a": _MARK_START, "b": _MARK_END})
        for doc_id, body, best in rows:
            snippets[doc_id] = body if _MARK_START in (body or "") else (best if _MARK_START in (best or "") else "")
    return [PaperHit(d, counts[d.id], " ".join((snippets.get(d.id) or "").split())) for d in ranked]


# --- a, continued: the AI reads papers that have no text layer ---------------------------------------

_READ_SYSTEM = (
    "You transcribe a household paper. Write out ALL the text printed on it, in its own language, in reading order, "
    "as plain text: no commentary, no translation, no markdown. Keep numbers, dates, names and amounts exactly as "
    "printed. For a photo with no text, write one short line describing what it shows.")


@dataclass
class TextRun:
    read: int = 0
    no_text: int = 0
    failed: int = 0
    waiting_for_level: int = 0
    left: int = 0                # still to read after this run (the next run takes them)


def missing_text_documents(session: Session) -> list[Document]:
    """Filed papers with no text yet: no row at all, or a failed AI read with attempts left. Oldest first."""
    rows = {r.document_id: r for r in session.exec(select(PaperText))}
    docs = session.exec(select(Document).where(Document.category.is_not(None),
                                               Document.status == DocumentStatus.PROCESSED)
                        .order_by(Document.id)).all()
    return [d for d in docs if d.id not in rows or (rows[d.id].method == FAILED and rows[d.id].attempts < MAX_ATTEMPTS)]


async def _ai_read(session: Session, document: Document, gateway) -> str:
    from app.llm_gateway import VISION_EXTRACTION
    from app.privacy.scope import privacy_scope
    from app.privacy.settings import configured_tier
    from app.privacy.tiers import Tier
    from app.services.document_input import build_content_block
    # The paper's own level rides on the call (its privacy scope); a High paper also says so itself, so it can never
    # go out lower while levels are not yet binding.
    high = configured_tier(session, document.category, document.paper_kind) == Tier.HIGH.value
    with privacy_scope(session, document.category, document.paper_kind):
        block = await asyncio.to_thread(build_content_block, document.file_path)
        result = await gateway.run(VISION_EXTRACTION, _READ_SYSTEM, "Transcribe this paper.", attachments=[block],
                                   sensitivity=Tier.HIGH.value if high else None)
    return (result.text or "").strip()[:_MAX_TEXT_CHARS]


async def read_missing_texts(session: Session, *, batch: int = 10, gateway=None) -> TextRun:
    """Give text to up to `batch` filed papers that have none: the PDF's own text when it has one (free), else the
    AI reads the page at the paper's level. A paper the AI cannot read (a video, a spreadsheet) is marked as having
    no text. A type with no level while levels are binding waits, untouched."""
    from app.llm_gateway import get_gateway
    from app.privacy.settings import TierHeld
    from app.problems import Problem, explain, record_problem, resolve_for
    from app.services.document_input import is_model_readable
    out = TextRun()
    todo = missing_text_documents(session)
    ai_calls = 0
    for doc in todo:
        text = pdf_text_of(doc.file_path)
        if text:
            _save_text(session, doc.id, text, PDF_TEXT)
            index_document(session, doc)
            out.read += 1
            continue
        path = Path(doc.file_path)
        if not is_model_readable(doc.file_path) or not path.exists() or path.stat().st_size > _MAX_AI_FILE_BYTES:
            _save_text(session, doc.id, "", NO_TEXT)
            out.no_text += 1
            continue
        if ai_calls >= batch:
            out.left += 1
            continue
        ai_calls += 1
        try:
            text = await _ai_read(session, doc, gateway or get_gateway())
        except TierHeld:
            out.waiting_for_level += 1
            continue
        except Exception as exc:
            session.rollback()
            row = _save_text(session, doc.id, "", FAILED, error=str(exc)[:500], attempt=True)
            out.failed += 1
            if row.attempts >= MAX_ATTEMPTS:
                problem = explain(f"The text of {doc.filename} wasn't read, so search finds it only by its title "
                                  f"and details.", str(exc))
                record_problem(session, area="Search", ref=f"document:{doc.id}:text", technical=str(exc),
                               problem=Problem(problem.what, problem.why, problem.next_step, "/schedules"))
            continue
        _save_text(session, doc.id, text, AI_READ, attempt=True)
        index_document(session, doc)
        resolve_for(session, f"document:{doc.id}:text")
        out.read += 1
    return out
