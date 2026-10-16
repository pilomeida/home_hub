"""What the Health tab shows: one row per health paper, filterable like a database."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from sqlmodel import Session, select

from app.domains.health import areas as areas_svc
from app.domains.health.categories import RECORD_KINDS
from app.domains.health.text import match_key
from app.models.document import Document, DocumentStatus
from app.models.domain import Domain
from app.models.health import HealthArea, HealthEntry, HealthPerson


@dataclass
class Filters:
    person: Optional[int] = None
    area: Optional[int] = None
    condition: Optional[int] = None
    year: Optional[int] = None
    kind: Optional[str] = None
    q: str = ""
    unsorted: bool = False
    check: bool = False


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_filters(params: Mapping[str, str]) -> Filters:
    kinds = {v for v, _ in RECORD_KINDS}
    kind = params.get("kind") or None
    return Filters(
        person=_int(params.get("person")), area=_int(params.get("area")), condition=_int(params.get("condition")),
        year=_int(params.get("year")), kind=kind if kind in kinds else None, q=(params.get("q") or "").strip(),
        unsorted=params.get("unsorted") == "1", check=params.get("check") == "1")


@dataclass
class Row:
    document: Document
    entry: Optional[HealthEntry]
    person: Optional[HealthPerson]
    areas: list[HealthArea] = field(default_factory=list)       # approved areas
    conditions: list[HealthArea] = field(default_factory=list)  # approved conditions
    proposed: list[HealthArea] = field(default_factory=list)    # proposed, not yet approved


@dataclass
class Facets:
    people: list[HealthPerson]
    years: list[int]
    kinds: list[tuple[str, str]]
    areas: list[HealthArea]
    conditions: list[HealthArea]
    proposed_areas: int
    unconfirmed_people: int
    needs_check: int
    unsorted: int


def _all_rows(session: Session) -> list[Row]:
    docs = session.exec(select(Document).where(
        Document.domain == Domain.HEALTH,
        Document.status.in_([DocumentStatus.PROCESSED, DocumentStatus.NEEDS_ATTENTION]))).all()
    entries = {e.document_id: e for e in session.exec(select(HealthEntry)).all()}
    people = {p.id: p for p in session.exec(select(HealthPerson)).all()}
    out = []
    for d in docs:
        e = entries.get(d.id)
        row = Row(d, e, people.get(e.person_id) if e else None)
        if e is not None:
            for t in areas_svc.tags_of(session, e.id):
                if t.merged_into_id is not None:
                    continue
                if not t.approved:
                    row.proposed.append(t)
                elif t.kind == "area":
                    row.areas.append(t)
                else:
                    row.conditions.append(t)
        out.append(row)
    return out


def rows(session: Session, f: Filters) -> list[Row]:
    result = []
    q = match_key(f.q)
    for r in _all_rows(session):
        e = r.entry
        if f.person is not None and (e is None or e.person_id != f.person):
            continue
        if f.year is not None and (e is None or e.year != f.year):
            continue
        if f.kind and (e is None or e.kind != f.kind):
            continue
        if f.area is not None and f.area not in {a.id for a in r.areas}:
            continue
        if f.condition is not None and f.condition not in {a.id for a in r.conditions}:
            continue
        if f.unsorted and r.areas:
            continue
        if f.check and not (e is None or e.needs_check):
            continue
        if q:
            hay = match_key(" ".join([r.document.filename, (e.provider or "") if e else "", (e.summary or "") if e else "",
                                      " ".join(a.name for a in r.areas + r.conditions + r.proposed)]))
            if not all(tok in hay for tok in q.split()):
                continue
        result.append(r)
    result.sort(key=lambda r: ((r.entry.entry_date.toordinal() if r.entry and r.entry.entry_date else 0), r.document.id),
                reverse=True)
    return result


def facets(session: Session) -> Facets:
    allr = _all_rows(session)
    areas = list(session.exec(select(HealthArea).where(HealthArea.kind == "area", HealthArea.approved.is_(True),
                                                       HealthArea.merged_into_id.is_(None)).order_by(HealthArea.name)))
    conds = list(session.exec(select(HealthArea).where(HealthArea.kind == "condition", HealthArea.approved.is_(True),
                                                       HealthArea.merged_into_id.is_(None)).order_by(HealthArea.name)))
    people = list(session.exec(select(HealthPerson).where(HealthPerson.merged_into_id.is_(None))
                               .order_by(HealthPerson.name)))
    proposed = session.exec(select(HealthArea).where(HealthArea.approved.is_(False), HealthArea.merged_into_id.is_(None))).all()
    return Facets(
        people=people,
        years=sorted({r.entry.year for r in allr if r.entry and r.entry.year}, reverse=True),
        kinds=list(RECORD_KINDS), areas=areas, conditions=conds, proposed_areas=len(proposed),
        unconfirmed_people=sum(1 for p in people if not p.confirmed),
        needs_check=sum(1 for r in allr if r.entry is None or r.entry.needs_check),
        unsorted=sum(1 for r in allr if not r.areas))
