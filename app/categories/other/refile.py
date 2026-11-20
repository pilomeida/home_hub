"""PLAN step 8b: the Other papers filed before contracts and letters had kinds of their own move to them, by the words
of their title and summary (code, never a model). A paper whose words say neither stays an "Other paper"; a person
can re-file any paper from its page."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlmodel import Session, select

from app.categories.fields import load_fields
from app.categories.other.spec import CONTRACT, LETTER, OTHER_PAPER
from app.models.category import Category
from app.models.document import Document

_CONTRACT = re.compile(r"\b(contrato|contract|agreement|acordo|nda|termos e condi[cç][oõ]es|terms and conditions|"
                       r"ades[aã]o|arrendamento|lease)\b", re.I)
_LETTER = re.compile(r"\b(carta|letter|fatca|crs|autocertifica[cç][aã]o|self[- ]certification|notifica[cç][aã]o|"
                     r"notification|aviso|notice|comunica[cç][aã]o|of[ií]cio)\b", re.I)


def kind_for(fields: dict) -> str:
    """Contract first (a contract's cover letter is still the contract), then letter, else Other paper."""
    words = " ".join(str(fields.get(k) or "") for k in ("title", "summary"))
    if _CONTRACT.search(words):
        return CONTRACT
    if _LETTER.search(words):
        return LETTER
    return OTHER_PAPER


@dataclass
class Move:
    document_id: int
    title: str
    to: str


def plan(session: Session) -> list[Move]:
    out = []
    for doc in session.exec(select(Document).where(Document.category == Category.OTHER,
                                                   Document.paper_kind == OTHER_PAPER)).all():
        fields = load_fields(doc)
        kind = kind_for(fields)
        if kind != OTHER_PAPER:
            out.append(Move(doc.id, fields.get("title") or doc.filename, kind))
    return out


def apply(session: Session) -> list[Move]:
    from app.services.paper_search import index_document
    moves = plan(session)
    for move in moves:
        doc = session.get(Document, move.document_id)
        doc.paper_kind = move.to
        session.add(doc)
        session.flush()
        index_document(session, doc)                 # the kind's name is part of what the search finds
    session.commit()
    return moves
