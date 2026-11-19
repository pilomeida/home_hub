"""Recognising known, recurring documents WITHOUT any AI (decision D13).

Rules are rows in `rules` (PLAN step 2: one memory of past answers; they were
`document_fingerprints` until 2026-11-19) whose answer is a kind of paper, readable and
editable on the Privacy screen. A document is recognised only if every rule that matches it points at
the same (domain, category); if rules disagree it is NOT recognised and a
human decides. Text comes only from places that cost nothing and send nothing:
the file name, the email subject/caption, and a PDF's own text layer (never OCR,
never a model). A photo or scan has no text layer, so it is recognised only by
its caption or sender."""

from __future__ import annotations

import io
import re
from email.utils import parseaddr
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from app.models.rule import CONTAINS, FILENAME, PERSON, SENDER, WORDS, Rule
from app.privacy.settings import known_types

KINDS = {
    "sender": "Comes from (sender address contains)",
    "words": "Contains the words (subject, caption or printed text)",
    "filename": "File name contains",
}
_ORIGINAL_SENDER = re.compile(r"^Originally from:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_MAX_PDF_PAGES = 3
_MAX_TEXT_CHARS = 20_000


@dataclass(frozen=True)
class Recognition:
    category: str
    paper_kind: str
    reasons: tuple[str, ...]          # plain-language, shown on the card


def pdf_text(content: bytes, filename: str) -> str:
    """The first pages' own text layer; "" for anything that is not a text PDF.
    Never raises: recognition must not be able to break intake."""
    if not filename.lower().endswith(".pdf"):
        return ""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        parts = [(page.extract_text() or "") for page in reader.pages[:_MAX_PDF_PAGES]]
        return "\n".join(parts)[:_MAX_TEXT_CHARS]
    except Exception:
        return ""


def original_sender(context_text: Optional[str]) -> str:
    m = _ORIGINAL_SENDER.search(context_text or "")
    if not m:
        return ""
    raw = m.group(1).strip()
    return (parseaddr(raw)[1] or raw).lower()


def _matches(rule: Rule, *, sender: str, haystack: str, filename: str) -> bool:
    needle = rule.pattern.strip().lower()
    if not needle:
        return False
    if rule.looks_at == SENDER:
        return needle in sender
    if rule.looks_at == WORDS:
        return needle in haystack
    if rule.looks_at == FILENAME:
        return needle in filename
    return False


def paper_kind_rules(session: Session) -> list[Rule]:
    """The switched-on rules that say what kind of paper something is, oldest first."""
    return list(session.exec(select(Rule).where(
        Rule.enabled == True, Rule.looks_at.in_((SENDER, WORDS, FILENAME)),  # noqa: E712
        Rule.category.is_not(None), Rule.paper_kind.is_not(None)).order_by(Rule.id)))


def recognise(session: Session, *, filename: str, content: bytes, context_text: Optional[str]) -> Optional[Recognition]:
    valid = {(i.category, i.paper_kind) for i in known_types()}
    rules = [r for r in paper_kind_rules(session) if (r.category, r.paper_kind) in valid]
    if not rules:
        return None
    sender = original_sender(context_text)
    haystack = ((context_text or "") + "\n" + pdf_text(content, filename)).lower()
    hits = [r for r in rules if _matches(r, sender=sender, haystack=haystack, filename=filename.lower())]
    if not hits or len({(r.category, r.paper_kind) for r in hits}) != 1:
        return None
    now = datetime.utcnow()
    for r in hits:
        r.hits += 1
        r.last_hit_at = now
        session.add(r)
    session.commit()
    return Recognition(hits[0].category, hits[0].paper_kind,
                       tuple(f"{KINDS[r.looks_at].split(' (')[0].lower()}: “{r.pattern}”" for r in hits))


def remember(session: Session, *, category: str, paper_kind: str, by: Optional[str], sender: str = "",
             words: str = "", note: str = "") -> list[Rule]:
    """Create the rules a human asked to remember (a person's rules). Existing identical rules are not duplicated."""
    from app.services import rules
    made = []
    for looks_at, pattern in ((SENDER, sender.strip().lower()), (WORDS, words.strip().lower())):
        if not pattern:
            continue
        exists = session.exec(select(Rule).where(Rule.looks_at == looks_at, Rule.how == CONTAINS, Rule.pattern == pattern,
                                                 Rule.category == category, Rule.paper_kind == paper_kind)).first()
        if exists is None:
            made.append(rules.remember(session, looks_at=looks_at, how=CONTAINS, pattern=pattern, source=PERSON, by=by,
                                       category=category, paper_kind=paper_kind,
                                       note=note or "Remembered when filing a document"))
    session.commit()
    return made
