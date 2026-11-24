"""Filing a paper under what the Hub read (PLAN step 12d): the paper goes by its new name everywhere (the name it came
in with is kept as where it came from), carries its tags and who it is about, and lists its items, one row each.

The name is built by the one formatter (W2, app/services/paper_names.py); the file itself is kept as received (Q4):
only the name changes, never the bytes or the place on disk. Who and the items are subjects (S5)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping, Optional

from sqlmodel import Session, select

from app.models.document import Document, PaperItem
from app.models.subject import SubjectKind
from app.services import subjects


def extension(document: Document) -> str:
    """The file's own ending (".pdf"), from the name it came in with: a name never changes what the file is."""
    return Path(document.original_filename or document.filename or "").suffix.lower()


def rename(document: Document, filed_name: Optional[str]) -> bool:
    """The paper goes by `filed_name` (plus its file's ending); the first name it had is kept. False: no change."""
    name = " ".join((filed_name or "").split())
    if not name:
        return False
    new = name + extension(document)
    if new == document.filename:
        return False
    if document.original_filename is None:
        document.original_filename = document.filename
    document.filename = new
    return True


def tags_of(document: Document) -> list[str]:
    try:
        tags = json.loads(document.tags_json or "[]")
    except ValueError:
        return []
    return [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []


def items_of(session: Session, document_id: int) -> list[PaperItem]:
    return list(session.exec(select(PaperItem).where(PaperItem.document_id == document_id).order_by(PaperItem.id)))


def file_under(session: Session, document: Document, guess: Mapping) -> None:
    """Name, tags, who and items from the approved guess. Not committed here: the approval commits it."""
    rename(document, guess.get("filed_name"))
    document.tags_json = json.dumps(list(guess.get("tags") or []), ensure_ascii=False)
    person = subjects.find(session, SubjectKind.PERSON, guess.get("who"))
    document.who_subject_id = person.id if person is not None else None
    session.add(document)
    for old in items_of(session, document.id):
        session.delete(old)
    for item in guess.get("items") or []:
        name = " ".join((item.get("name") or "").split())
        if not name:
            continue
        thing = subjects.register(session, SubjectKind.THING, name)
        session.add(PaperItem(document_id=document.id, name=name, room=item.get("room") or None,
                              warranty_end=item.get("warranty_end") or None,
                              subject_id=thing.id if thing is not None else None))
    session.flush()


# --- naming a paper already filed (PLAN step 12e) ----------------------------------------------------------------

def known_parts(session: Session, document: Document) -> dict:
    """What the Hub already holds of a filed paper for its name: {"paper_date", "what", "who"}, any of them empty.
    An area that says how (`CategorySpec.name_parts`) is asked; otherwise its fields, by `from_guess`."""
    from app.categories.fields import load_fields
    from app.categories.registry import get_spec, is_implemented
    if not is_implemented(document.category):
        return {}
    spec = get_spec(document.category)
    if spec.name_parts is not None:
        parts = dict(spec.name_parts(session, document) or {})
    else:
        fields = load_fields(document)
        pick = lambda wanted: next((fields[k] for k, part in spec.from_guess.items()              # noqa: E731
                                    if part in wanted and fields.get(k)), "")
        parts = {"paper_date": pick(("paper_date",)), "what": pick(("what", "item")), "who": pick(("who",))}
    if not parts.get("paper_date"):
        day = spec.paper_date(session, document)
        parts["paper_date"] = day.isoformat() if day else ""
    return parts


_NAME_DAYS = (re.compile(r"(?<!\d)(\d{4})-?(\d{2})-?(\d{2})(?!\d)"),                # 20250723, 2023-07-25
              re.compile(r"(?<!\d)(\d{2})[_.-](\d{2})[_.-](\d{4})(?!\d)"))          # 02_08_2026 (day first)


def date_in_name(document: Document) -> str:
    """A date written in the name the paper came in with ("detalhe_fatura_20250723.pdf"), checked as a real day
    (Q1), else "". It comes after the paper's own date and before the day it arrived."""
    name = document.original_filename or document.filename or ""
    for n, pattern in enumerate(_NAME_DAYS):
        for match in pattern.finditer(name):
            y, m, d = match.groups() if n == 0 else reversed(match.groups())
            day = _real_day(f"{y}-{m}-{d}")
            if day:
                return day
    return ""


def name_from(session: Session, document: Document, parts: Mapping) -> str:
    from app.categories.registry import get_spec
    from app.services.paper_names import filed_name
    spec = get_spec(document.category)
    kind = spec.kind_label(document.paper_kind) if document.paper_kind else spec.label
    return filed_name(paper_date=parts.get("paper_date"), received=document.created_at, kind=kind,
                      what=parts.get("what"), who=parts.get("who"))


_WHAT_SYSTEM = """You name one household paper from its text. Return ONLY a JSON object
{"what": "...", "paper_date": "YYYY-MM-DD or null"}. what: what the paper is in at most 8 words, in its own language:
issuer and subject, no amounts, no reference numbers, no names of people; e.g. "Fatura Vodafone detalhe de chamadas";
"" if the text says nothing about it. paper_date: the date the paper was issued, as printed (dd/mm/aaaa in Portuguese
papers), or null."""
_WHAT_SCHEMA = {"type": "object", "required": ["what", "paper_date"],
                "properties": {"what": {"type": "string"}, "paper_date": {"type": ["string", "null"]}}}
_TEXT_CHARS = 6000


async def ai_parts(session: Session, document: Document, gateway=None) -> dict:
    """Where the Hub holds too little: the AI reads the paper's stored text at the paper's own level, the same way
    search's Paper text job reads a paper (privacy scope; a High paper says so on the call). Its answer is checked
    by code (Q1): "what" a short string or "", "paper_date" a real date or "". Raises TierHeld when the kind has no
    level while levels bind."""
    from app.llm_gateway import CLASSIFICATION, get_gateway
    from app.privacy.scope import privacy_scope
    from app.privacy.settings import configured_tier
    from app.privacy.tiers import Tier
    from app.services.json_utils import strip_json_fences
    from app.services.paper_search import text_row
    row = text_row(session, document.id)
    text = (row.text if row is not None else "") or ""
    if not text.strip():
        return {"what": "", "paper_date": ""}
    high = configured_tier(session, document.category, document.paper_kind) == Tier.HIGH.value
    with privacy_scope(session, document.category, document.paper_kind):
        result = await (gateway or get_gateway()).run(
            CLASSIFICATION, _WHAT_SYSTEM, text[:_TEXT_CHARS], response_schema=_WHAT_SCHEMA,
            sensitivity=Tier.HIGH.value if high else None)
    try:
        answer = json.loads(strip_json_fences(result.text or ""))
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        return {"what": "", "paper_date": ""}
    what, day = answer.get("what"), answer.get("paper_date")
    return {"what": " ".join(what.split())[:120] if isinstance(what, str) else "", "paper_date": _real_day(day)}


def _real_day(value) -> str:
    from datetime import date
    try:
        day = date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return ""
    return day.isoformat() if day and 1900 <= day.year <= date.today().year + 1 else ""
