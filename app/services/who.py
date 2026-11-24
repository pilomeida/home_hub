"""The Who chip (PLAN step 10i): Everyone, or one person, on Papers, Coming up, Waiting for you and Ask.

People come from the subjects registry (each alias counts as its person). A paper is about a person when the Hub
already links them: a Health paper through its person, a bank paper through the owner of the account its lines are
on. Choosing a person is a filter for whoever is looking, kept in a cookie: it hides nothing from anyone's login,
and "Everyone" shows everything as before."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, select

from app.models.account import Account
from app.models.document import Document
from app.models.health import HealthEntry, HealthPerson
from app.models.subject import Subject, SubjectKind
from app.models.transaction import Transaction

COOKIE = "who"


@dataclass(frozen=True)
class Person:
    id: int
    name: str


def _root(by_id: dict[int, Subject], sid: Optional[int]) -> Optional[int]:
    seen = set()
    while sid is not None and sid in by_id and by_id[sid].merged_into_id and sid not in seen:
        seen.add(sid)
        sid = by_id[sid].merged_into_id
    return sid if sid in by_id else None


def _subjects(session: Session) -> dict[int, Subject]:
    return {s.id: s for s in session.exec(select(Subject).where(Subject.kind == SubjectKind.PERSON)).all()}


def people(session: Session) -> list[Person]:
    return sorted((Person(s.id, s.name) for s in _subjects(session).values() if not s.merged_into_id),
                  key=lambda p: p.name.lower())


def chosen(session: Session, cookie: Optional[str]) -> Optional[Person]:
    """The person a cookie names, or None (Everyone) for no cookie, a removed person or anything else."""
    if not cookie or not str(cookie).isdigit():
        return None
    by_id = _subjects(session)
    sid = _root(by_id, int(cookie))
    return Person(sid, by_id[sid].name) if sid is not None else None


def document_ids(session: Session, person_id: int) -> set[int]:
    """The papers the Hub links to this person: Health papers, bank papers of their accounts, and papers filed as
    about them."""
    by_id = _subjects(session)
    out: set[int] = set()
    hp = {p.id: p for p in session.exec(select(HealthPerson)).all()}
    for e in session.exec(select(HealthEntry).where(HealthEntry.person_id.is_not(None))).all():
        p = hp.get(e.person_id)
        while p is not None and p.merged_into_id and p.merged_into_id in hp:
            p = hp[p.merged_into_id]
        if p is not None and _root(by_id, p.subject_id) == person_id:
            out.add(e.document_id)
    for doc_id, sid in session.exec(select(Document.id, Document.who_subject_id)
                                    .where(Document.who_subject_id.is_not(None))).all():
        if _root(by_id, sid) == person_id:                   # filed under who it is about (PLAN step 12d)
            out.add(doc_id)
    mine = [a.id for a in session.exec(select(Account)).all() if _root(by_id, a.owner_subject_id) == person_id]
    if mine:
        out |= set(session.exec(select(Transaction.document_id).where(Transaction.account_id.in_(mine))).all())
    return out


def only(groups: list, ids: set[int]) -> list:
    """A people page's groups (title, papers) with only the chosen person's papers; empty groups left out. A paper
    is a tuple whose first part is the document, or anything with a `.document`."""
    def doc(p):
        return getattr(p, "document", None) or (p[0] if isinstance(p, tuple) else p.get("document"))
    kept = [(title, [p for p in papers if doc(p) is not None and doc(p).id in ids]) for title, papers in groups]
    return [(title, papers) for title, papers in kept if papers]


def header(cookie: Optional[str]) -> tuple[list[Person], Optional[Person]]:
    """For the header chip: the people, and the one chosen (None: Everyone). Opens its own session; never raises
    (a menu must not break a page)."""
    try:
        from app.db import engine
        with Session(engine) as s:
            return people(s), chosen(s, cookie)
    except Exception:
        return [], None


def safe_back(url: Optional[str]) -> str:
    """Only a path on this site (never another site) is a place to go back to."""
    return url if url and url.startswith("/") and not url.startswith("//") else "/"


__all__ = ["COOKIE", "Person", "people", "chosen", "document_ids", "only", "header", "safe_back"]
