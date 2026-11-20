"""Health, new look (PLAN step 10f): a tile per person (no faces), the follow-ups across the family, papers by area;
and a person's page with their conditions, their results drawn against the printed reference range, and a timeline.

Presentation only: the rows come from the Health records list (`browse`), the results from `tests.results_of`, the
follow-ups from the Health to-dos. Nothing here reads anything the records page did not already show."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.categories.health import browse
from app.categories.health.kinds import KIND_LABELS
from app.categories.health.tests import results_of
from app.models.category import Category
from app.models.health import HealthEntry, HealthPerson
from app.models.todo import Todo
from app.ui.charts import Item

MAX_POINTS = 8                    # a result's chart shows its last eight measurements; the table keeps every one


@dataclass
class FollowUp:
    due: Optional[date]
    who: str
    what: str
    link: str
    note: str                     # "in 5 days" / "today" / "overdue" / "no date"
    late: bool = False


def _note(due: Optional[date], today: date) -> tuple[str, bool]:
    if due is None:
        return "no date", False
    days = (due - today).days
    if days < 0:
        return "overdue", True
    return ("today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"), False


def _entry_people(session: Session) -> tuple[dict[int, HealthEntry], dict[int, HealthPerson]]:
    entries = {e.document_id: e for e in session.exec(select(HealthEntry)).all()}
    people = {p.id: p for p in session.exec(select(HealthPerson)).all()}
    return entries, people


def _person_of(person: Optional[HealthPerson], people: dict[int, HealthPerson]) -> Optional[HealthPerson]:
    seen = set()
    while person is not None and person.merged_into_id and person.id not in seen:     # an alias counts as its person
        seen.add(person.id)
        person = people.get(person.merged_into_id)
    return person


def follow_ups(session: Session, today: date, person_id: Optional[int] = None) -> list[FollowUp]:
    """Open Health to-dos, soonest first (overdue on top), undated last; each with the person its paper is about."""
    entries, people = _entry_people(session)
    out = []
    for t in session.exec(select(Todo).where(Todo.category == Category.HEALTH, Todo.done.is_(False))).all():
        entry = entries.get(t.document_id) if t.document_id else None
        person = _person_of(people.get(entry.person_id) if entry and entry.person_id else None, people)
        if person_id is not None and (person is None or person.id != person_id):
            continue
        note, late = _note(t.due_date, today)
        out.append(FollowUp(t.due_date, person.name if person else "—", t.title,
                            f"/health/documents/{t.document_id}" if t.document_id else "/todos", note, late))
    out.sort(key=lambda f: (f.due is None, f.due or date.max))
    return out


@dataclass
class Tile:
    person: HealthPerson
    papers: int
    latest: Optional[str]          # "Lab result 2026-09-12"
    next: Optional[FollowUp]
    link: str


@dataclass
class AreaCount:
    name: str
    kind: str                      # "area" | "condition"
    papers: int
    link: str


@dataclass
class HealthHome:
    tiles: list[Tile]
    follow_ups: list[FollowUp]
    areas: list[AreaCount]
    proposed: int                  # areas or conditions proposed, not approved yet
    unsorted: int


def _label(entry: Optional[HealthEntry]) -> Optional[str]:
    if entry is None:
        return None
    kind = KIND_LABELS.get(entry.kind or "", "Paper")
    return f"{kind} {entry.entry_date.isoformat()}" if entry.entry_date else kind


def health_home(session: Session, today: Optional[date] = None) -> HealthHome:
    today = today or date.today()
    rows = browse.rows(session, browse.Filters())               # newest first, as the records list
    people = {p.id: p for p in session.exec(select(HealthPerson)).all()}
    ups = follow_ups(session, today)
    tiles = []
    for p in session.exec(select(HealthPerson).where(HealthPerson.merged_into_id.is_(None))
                          .order_by(HealthPerson.name)).all():
        mine = [r for r in rows if r.person is not None and _person_of(r.person, people).id == p.id]
        nxt = next((f for f in ups if f.who == p.name and f.due is not None), None)
        tiles.append(Tile(p, len(mine), _label(mine[0].entry) if mine else None, nxt, f"/health/people/{p.id}"))
    counts: dict[int, AreaCount] = {}
    for r in rows:
        for a in r.areas + r.conditions:
            c = counts.setdefault(a.id, AreaCount(a.name, a.kind, 0, f"/health/records?{a.kind}={a.id}"))
            c.papers += 1
    facets = browse.facets(session)
    return HealthHome(tiles, ups, sorted(counts.values(), key=lambda c: (c.kind != "area", c.name.lower())),
                      facets.proposed_areas, facets.unsorted)


@dataclass
class ResultLine:
    name: str
    unit: Optional[str]
    approved: bool
    latest: dict
    points: list[dict]
    ref: Optional[tuple]           # (low, high) printed on the latest paper, when it has one
    items: list[Item] = field(default_factory=list)

    @property
    def numeric(self) -> bool:
        return len(self.items) >= 2


@dataclass
class TimelineRow:
    when: Optional[date]
    what: str
    note: str
    link: str
    ahead: bool = False


@dataclass
class PersonPage:
    person: HealthPerson
    papers: int
    conditions: list[AreaCount]
    results: list[ResultLine]
    timeline: list[TimelineRow]


def person_page(session: Session, person_id: int, today: Optional[date] = None) -> Optional[PersonPage]:
    today = today or date.today()
    person = session.get(HealthPerson, person_id)
    if person is None or person.merged_into_id:
        return None
    people = {p.id: p for p in session.exec(select(HealthPerson)).all()}
    rows = [r for r in browse.rows(session, browse.Filters())
            if r.person is not None and _person_of(r.person, people).id == person.id]
    conds: dict[int, AreaCount] = {}
    for r in rows:
        for a in r.conditions:
            c = conds.setdefault(a.id, AreaCount(a.name, a.kind, 0, f"/health/records?condition={a.id}&person={person.id}"))
            c.papers += 1
    results = []
    for line in results_of(session, person.id):
        latest = line["latest"]
        ref = latest.get("ref")
        drawn = [p for p in line["points"] if p["numeric"] is not None][:MAX_POINTS][::-1]
        items = [Item(p["date"].isoformat()[:7], p["numeric"], current=(i == len(drawn) - 1))
                 for i, p in enumerate(drawn)]
        results.append(ResultLine(line["test"].name, line["test"].unit, line["test"].approved, latest, line["points"],
                                  ref if ref and any(v is not None for v in ref) else None, items))
    timeline = [TimelineRow(f.due, f.what, f.note, f.link, True) for f in follow_ups(session, today, person.id)
                if f.due is not None and f.due >= today]
    timeline.sort(key=lambda t: t.when, reverse=True)
    for r in rows:
        e = r.entry
        timeline.append(TimelineRow(e.entry_date if e else None, KIND_LABELS.get(e.kind or "", "Paper") if e else "Paper",
                                    (e.provider or "") if e else "couldn't be read",
                                    f"/health/documents/{r.document.id}"))
    return PersonPage(person, len(rows), sorted(conds.values(), key=lambda c: c.name.lower()), results, timeline)


__all__ = ["FollowUp", "follow_ups", "Tile", "AreaCount", "HealthHome", "health_home", "ResultLine", "TimelineRow",
           "PersonPage", "person_page", "MAX_POINTS"]
