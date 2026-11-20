"""Health conditions (PLAN step 7a): a long-running condition of one person, with its own page where its papers come
together. The Hub only proposes; a person confirms (Pedro, 2026-10-19, Q2).

- A condition is created by a person: from a proposal (a condition the reader named on a paper, kept as the paper's
  old condition label) or by hand ("propose a new condition" on a paper or a person's page).
- Which papers are about it is suggested by code, never a second model call: the names the reader gave a paper are
  matched against the person's conditions (one name inside the other, or a small list of names that mean the same);
  a person confirms, picks another, or says none.
- A condition is a subject (step 6), so its facts can be found from it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from sqlmodel import Session, select

from app.categories.health.text import match_key
from app.models.health import (HealthArea, HealthCondition, HealthEntry, HealthEntryCondition, HealthEntryTag,
                               HealthPerson)
from app.models.subject import SubjectKind
from app.problems import Problem

SUGGESTED, CONFIRMED, REJECTED = "suggested", "confirmed", "rejected"
STATUSES = {"followed": "Followed", "stable": "Stable", "resolved": "Resolved"}

# Names that mean the same condition whatever the wording (Claude, 2026-11-20; grows as papers come).
SAME_CONDITION = (
    frozenset({"avc", "avc isquemico", "acidente vascular cerebral", "stroke", "ischaemic stroke", "ischemic stroke"}),
    frozenset({"hta", "hipertensao", "hipertensao arterial", "hypertension", "high blood pressure"}),
    frozenset({"diabetes", "diabetes mellitus", "dm2", "type 2 diabetes"}),
    frozenset({"placa ateromatosa", "atheromatous plaque", "atherosclerosis", "aterosclerose"}),
)


class ConditionError(Exception):
    def __init__(self, problem: Problem):
        super().__init__(problem.text)
        self.problem = problem


def _person(session: Session, person_id: int) -> HealthPerson:
    person = session.get(HealthPerson, person_id)
    while person is not None and person.merged_into_id:
        person = session.get(HealthPerson, person.merged_into_id)
    if person is None:
        raise ConditionError(Problem("Not saved.", "That person is no longer listed in Health.",
                                     "Reload the page and choose the person again."))
    return person


def alike(a: str, b: str) -> bool:
    """Two names of the same condition: equal, one inside the other, or in the same list of names."""
    ka, kb = match_key(a), match_key(b)
    if not ka or not kb:
        return False
    if ka == kb or (len(min(ka, kb, key=len)) >= 4 and (ka in kb or kb in ka)):
        return True
    return any(ka in group and kb in group for group in SAME_CONDITION)


def conditions_of(session: Session, person_id: int) -> list[HealthCondition]:
    person = _person(session, person_id)
    return list(session.exec(select(HealthCondition).where(HealthCondition.person_id == person.id)
                             .order_by(HealthCondition.name)).all())


def _entries_of(session: Session, person_id: int) -> list[HealthEntry]:
    ids = {p.id for p in session.exec(select(HealthPerson)).all()
           if p.id == person_id or _root_id(session, p) == person_id}
    return list(session.exec(select(HealthEntry).where(HealthEntry.person_id.in_(ids))).all())


def _root_id(session: Session, person: HealthPerson) -> int:
    seen = set()
    while person.merged_into_id and person.id not in seen:
        seen.add(person.id)
        nxt = session.get(HealthPerson, person.merged_into_id)
        if nxt is None:
            break
        person = nxt
    return person.id


def named_on(session: Session, entry: HealthEntry) -> list[HealthArea]:
    """The condition names the reader gave this paper (its old condition labels), each followed to the name kept."""
    rows = session.exec(select(HealthArea).join(HealthEntryTag, HealthEntryTag.area_id == HealthArea.id)
                        .where(HealthEntryTag.entry_id == entry.id, HealthArea.kind == "condition")).all()
    return list(rows)


def suggest_for_entry(session: Session, entry: HealthEntry) -> int:
    """Suggest the person's conditions this paper's names fit; a decision a person already made is kept. Returns how
    many suggestions were added."""
    if entry.person_id is None:
        return 0
    person_id = _root_id(session, session.get(HealthPerson, entry.person_id))
    names = [a.name for a in named_on(session, entry)]
    added = 0
    for condition in session.exec(select(HealthCondition).where(HealthCondition.person_id == person_id)).all():
        if not any(alike(n, condition.name) for n in names):
            continue
        if session.get(HealthEntryCondition, (entry.id, condition.id)) is None:
            session.add(HealthEntryCondition(entry_id=entry.id, condition_id=condition.id, state=SUGGESTED))
            added += 1
    session.flush()
    return added


def create(session: Session, person_id: int, name: str, by: Optional[str], *, started_on: Optional[date] = None,
           started_note: Optional[str] = None, followed_by: Optional[str] = None,
           confirm_entry_id: Optional[int] = None) -> HealthCondition:
    """A person creates a condition. Then every paper of that person whose names fit it is suggested (the paper it was
    created from, if any, is confirmed)."""
    person = _person(session, person_id)
    key = match_key(name)
    if not key:
        raise ConditionError(Problem("Not created.", "The condition has no name.", "Type its name, then press Create."))
    clash = session.exec(select(HealthCondition).where(HealthCondition.person_id == person.id,
                                                       HealthCondition.match_key == key)).first()
    if clash is not None:
        raise ConditionError(Problem("Not created.", f"{person.name} already has the condition {clash.name}.",
                                     "Open it from their page, or choose another name."))
    from app.services import subjects
    subject = subjects.register(session, SubjectKind.CONDITION, f"{name.strip()} — {person.name}")
    condition = HealthCondition(person_id=person.id, subject_id=subject.id if subject else None, name=name.strip(),
                                match_key=key, started_on=started_on, started_note=started_note or None,
                                followed_by=followed_by or None, created_by=by)
    session.add(condition)
    session.flush()
    for entry in _entries_of(session, person.id):
        suggest_for_entry(session, entry)
    if confirm_entry_id is not None:
        decide(session, confirm_entry_id, condition.id, CONFIRMED, by, commit=False)
    session.commit()
    session.refresh(condition)
    return condition


def decide(session: Session, entry_id: int, condition_id: int, state: str, by: Optional[str], *,
           commit: bool = True) -> None:
    """Confirm or reject that a paper is about a condition (confirming one the Hub did not suggest is "pick another")."""
    entry, condition = session.get(HealthEntry, entry_id), session.get(HealthCondition, condition_id)
    if entry is None or condition is None or state not in (CONFIRMED, REJECTED):
        raise ConditionError(Problem("Not saved.", "That paper or condition is no longer there.",
                                     "Reload the page to see what is still listed."))
    if entry.person_id is None or _root_id(session, session.get(HealthPerson, entry.person_id)) != condition.person_id:
        raise ConditionError(Problem("Not saved.", f"This paper is not about {_person(session, condition.person_id).name}, "
                                     "whose condition it is.", "Pick one of this person's conditions instead."))
    link = session.get(HealthEntryCondition, (entry_id, condition_id)) or HealthEntryCondition(
        entry_id=entry_id, condition_id=condition_id)
    link.state, link.decided_by, link.decided_at = state, by, datetime.utcnow()
    session.add(link)
    if commit:
        session.commit()


def none_of_these(session: Session, entry_id: int, by: Optional[str]) -> int:
    """The paper is about none of the suggested conditions: every open suggestion is rejected."""
    n = 0
    for link in session.exec(select(HealthEntryCondition).where(HealthEntryCondition.entry_id == entry_id,
                                                                HealthEntryCondition.state == SUGGESTED)).all():
        link.state, link.decided_by, link.decided_at = REJECTED, by, datetime.utcnow()
        session.add(link)
        n += 1
    session.commit()
    return n


def update(session: Session, condition_id: int, *, status: str, started_on: Optional[date], started_note: str,
           followed_by: str) -> HealthCondition:
    condition = session.get(HealthCondition, condition_id)
    if condition is None:
        raise ConditionError(Problem("Not saved.", "That condition is no longer listed.", "Reload the page."))
    if status not in STATUSES:
        raise ConditionError(Problem("Not saved.", f"“{status}” is not a status a condition can have.",
                                     "Choose Followed, Stable or Resolved."))
    condition.status, condition.started_on = status, started_on
    condition.started_note, condition.followed_by = started_note.strip() or None, followed_by.strip() or None
    session.add(condition)
    session.commit()
    return condition


# --- proposals: a condition the reader named that the person does not have yet ----------------------------------

@dataclass
class Proposal:
    person: HealthPerson
    name: str                       # as the reader wrote it (the old label, followed to the name kept)
    area_id: int
    papers: list[int] = field(default_factory=list)     # entry ids

    @property
    def ref(self) -> str:
        return f"{self.person.id}:{self.area_id}"


def proposals(session: Session) -> list[Proposal]:
    """For each person: the condition names on their papers that fit none of their conditions and that nobody has
    turned down (an old label a person said is not a condition is approved-and-dismissed)."""
    out: dict[tuple[int, int], Proposal] = {}
    for entry in session.exec(select(HealthEntry).where(HealthEntry.person_id.is_not(None))).all():
        person = session.get(HealthPerson, _root_id(session, session.get(HealthPerson, entry.person_id)))
        mine = session.exec(select(HealthCondition).where(HealthCondition.person_id == person.id)).all()
        for area in named_on(session, entry):
            area = _follow_area(session, area)
            if area.approved or any(alike(area.name, c.name) for c in mine):
                continue
            p = out.setdefault((person.id, area.id), Proposal(person, area.name, area.id))
            p.papers.append(entry.id)
    return sorted(out.values(), key=lambda p: (p.person.name, p.name))


def _follow_area(session: Session, area: HealthArea) -> HealthArea:
    seen = set()
    while area.merged_into_id and area.id not in seen:
        seen.add(area.id)
        area = session.get(HealthArea, area.merged_into_id) or area
    return area


def accept_proposal(session: Session, person_id: int, area_id: int, by: Optional[str]) -> HealthCondition:
    """Create the proposed condition for the person; its papers are confirmed (they named it)."""
    area = session.get(HealthArea, area_id)
    if area is None:
        raise ConditionError(Problem("Not created.", "That proposal is no longer there.", "Reload the page."))
    area = _follow_area(session, area)
    existing = next((c for c in conditions_of(session, person_id) if alike(c.name, area.name)), None)
    condition = existing or create(session, person_id, area.name, by)
    for entry in _entries_of(session, condition.person_id):
        if any(_follow_area(session, a).id == area.id for a in named_on(session, entry)):
            decide(session, entry.id, condition.id, CONFIRMED, by, commit=False)
    session.commit()
    return condition


def dismiss_proposal(session: Session, area_id: int) -> None:
    """Not a condition: the old label is set aside (approved, so it is no longer proposed to anyone)."""
    area = session.get(HealthArea, area_id)
    if area is None:
        raise ConditionError(Problem("Not saved.", "That proposal is no longer there.", "Reload the page."))
    area = _follow_area(session, area)
    area.approved = True
    session.add(area)
    session.commit()


# --- what pages show -------------------------------------------------------------------------------------------

@dataclass
class EntryBox:
    """The box on a paper: what it is confirmed to be about, what the Hub suggests, the person's other conditions."""
    confirmed: list[HealthCondition]
    suggested: list[HealthCondition]
    others: list[HealthCondition]
    proposed: list[str]                # names on this paper that fit none of the person's conditions
    person_id: Optional[int]


def entry_box(session: Session, entry: Optional[HealthEntry]) -> Optional[EntryBox]:
    if entry is None or entry.person_id is None:
        return None
    person_id = _root_id(session, session.get(HealthPerson, entry.person_id))
    links = {l.condition_id: l.state for l in session.exec(
        select(HealthEntryCondition).where(HealthEntryCondition.entry_id == entry.id)).all()}
    mine = conditions_of(session, person_id)
    confirmed = [c for c in mine if links.get(c.id) == CONFIRMED]
    suggested = [c for c in mine if links.get(c.id) == SUGGESTED]
    others = [c for c in mine if c.id not in links or links[c.id] == REJECTED]
    proposed = [a.name for a in named_on(session, entry) if not _follow_area(session, a).approved
                and not any(alike(a.name, c.name) for c in mine)]
    return EntryBox(confirmed, suggested, others, proposed, person_id)


def papers_of(session: Session, condition_id: int, state: str = CONFIRMED) -> list[HealthEntry]:
    rows = session.exec(select(HealthEntry).join(HealthEntryCondition, HealthEntryCondition.entry_id == HealthEntry.id)
                        .where(HealthEntryCondition.condition_id == condition_id,
                               HealthEntryCondition.state == state)).all()
    return sorted(rows, key=lambda e: e.entry_date or date.min, reverse=True)


def open_suggestions(session: Session) -> list[tuple[HealthEntry, HealthCondition]]:
    rows = session.exec(select(HealthEntryCondition).where(HealthEntryCondition.state == SUGGESTED)).all()
    return [(session.get(HealthEntry, l.entry_id), session.get(HealthCondition, l.condition_id)) for l in rows]


def forget_entry(session: Session, entry_id: int) -> None:
    """A paper deleted: its links go with it (before the entry, for the foreign keys)."""
    for link in session.exec(select(HealthEntryCondition).where(HealthEntryCondition.entry_id == entry_id)).all():
        session.delete(link)


__all__ = ["HealthCondition", "ConditionError", "STATUSES", "SUGGESTED", "CONFIRMED", "REJECTED", "alike",
           "conditions_of", "suggest_for_entry", "create", "decide", "none_of_these", "update", "Proposal",
           "proposals", "accept_proposal", "dismiss_proposal", "EntryBox", "entry_box", "papers_of",
           "open_suggestions", "forget_entry"]
