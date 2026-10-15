"""Recognising known, recurring documents WITHOUT any AI (decision D13).

Rules are rows in document_fingerprints, readable and editable on the Privacy
screen. A document is recognised only if every rule that matches it points at
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

from app.models.privacy import DocumentFingerprint
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
    domain: str
    category: str
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


def _matches(rule: DocumentFingerprint, *, sender: str, haystack: str, filename: str) -> bool:
    needle = rule.pattern.strip().lower()
    if not needle:
        return False
    if rule.kind == "sender":
        return needle in sender
    if rule.kind == "words":
        return needle in haystack
    if rule.kind == "filename":
        return needle in filename
    return False


def recognise(session: Session, *, filename: str, content: bytes, context_text: Optional[str]) -> Optional[Recognition]:
    valid = {(i.domain, i.category) for i in known_types()}
    rules = [r for r in session.exec(select(DocumentFingerprint)) if (r.domain, r.category) in valid]
    if not rules:
        return None
    sender = original_sender(context_text)
    haystack = ((context_text or "") + "\n" + pdf_text(content, filename)).lower()
    hits = [r for r in rules if _matches(r, sender=sender, haystack=haystack, filename=filename.lower())]
    if not hits or len({(r.domain, r.category) for r in hits}) != 1:
        return None
    now = datetime.utcnow()
    for r in hits:
        r.hits += 1
        r.last_hit_at = now
        session.add(r)
    session.commit()
    return Recognition(hits[0].domain, hits[0].category,
                       tuple(f"{KINDS[r.kind].split(' (')[0].lower()}: “{r.pattern}”" for r in hits))


def remember(session: Session, *, domain: str, category: str, by: Optional[str], sender: str = "",
             words: str = "", note: str = "") -> list[DocumentFingerprint]:
    """Create the rules a human asked to remember. Existing identical rules are not duplicated."""
    made = []
    for kind, pattern in (("sender", sender.strip().lower()), ("words", words.strip().lower())):
        if not pattern:
            continue
        exists = session.exec(select(DocumentFingerprint).where(
            DocumentFingerprint.kind == kind, DocumentFingerprint.pattern == pattern,
            DocumentFingerprint.domain == domain, DocumentFingerprint.category == category)).first()
        if exists is None:
            rule = DocumentFingerprint(kind=kind, pattern=pattern, domain=domain, category=category,
                                       note=note or "Remembered when filing a document", created_by=by)
            session.add(rule)
            made.append(rule)
    session.commit()
    return made
