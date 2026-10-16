"""Whose a paper is. People are discovered from the papers and confirmed once (Pedro, Q12); a name that is
exactly or uniquely a part of a known name is the same person, anything else is a new, unconfirmed person."""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from app.domains.health.text import match_key
from app.models.health import HealthEntry, HealthMeasurement, HealthPerson
from app.problems import Problem


class PersonError(Exception):
    def __init__(self, problem: Problem, status: int = 400):
        super().__init__(problem.text)
        self.problem = problem
        self.status = status


def _gone(what: str) -> PersonError:
    return PersonError(Problem(what, "That entry no longer exists.", "Reload the page to see what is still there."), 404)


def all_people(session: Session) -> list[HealthPerson]:
    """The people to show and choose from: a merged-away spelling is an alias, not a person."""
    return list(session.exec(select(HealthPerson).where(HealthPerson.merged_into_id.is_(None))
                             .order_by(HealthPerson.name)).all())


def _resolve(session: Session, person: HealthPerson) -> HealthPerson:
    """Follow a merged-away spelling to the person it was merged into."""
    seen = {person.id}
    while person.merged_into_id is not None and person.merged_into_id not in seen:
        nxt = session.get(HealthPerson, person.merged_into_id)
        if nxt is None:
            break
        seen.add(nxt.id)
        person = nxt
    return person


def find_or_create_person(session: Session, name: Optional[str]) -> Optional[HealthPerson]:
    key = match_key(name or "")
    if not key:
        return None
    exact = session.exec(select(HealthPerson).where(HealthPerson.match_key == key)
                         .order_by(HealthPerson.merged_into_id.is_(None).desc(), HealthPerson.id)).first()
    if exact is not None:                        # also a merged-away spelling: it leads to the person kept
        return _resolve(session, exact)
    people = all_people(session)
    tokens = set(key.split())
    if len(tokens) >= 2:
        related = [p for p in people if len(p.match_key.split()) >= 2
                   and (tokens <= set(p.match_key.split()) or set(p.match_key.split()) <= tokens)]
        if len(related) == 1:
            return related[0]
    person = HealthPerson(name=name.strip(), match_key=key, confirmed=False)
    session.add(person)
    session.commit()
    session.refresh(person)
    return person


def confirm_person(session: Session, person_id: int) -> None:
    person = session.get(HealthPerson, person_id)
    if person is None:
        raise _gone("Not confirmed.")
    person.confirmed = True
    session.add(person)
    session.commit()


def merge_people(session: Session, source_id: int, target_id: int) -> None:
    source = session.get(HealthPerson, source_id)
    target = session.get(HealthPerson, target_id)
    if source is None or target is None:
        raise _gone("Not merged.")
    if source.id == target.id:
        raise PersonError(Problem("Not merged.", f"{source.name} cannot be merged into themselves.",
                                  "Pick a different person to keep, then press Merge."))
    if source.merged_into_id is not None or target.merged_into_id is not None:
        gone = source if source.merged_into_id is not None else target
        raise PersonError(Problem("Not merged.", f"{gone.name} was already merged into someone else.",
                                  "Reload the page and pick from the people still listed."))
    for model in (HealthEntry, HealthMeasurement):
        for row in session.exec(select(model).where(model.person_id == source_id)).all():
            row.person_id = target_id
            session.add(row)
    for alias in session.exec(select(HealthPerson).where(HealthPerson.merged_into_id == source_id)).all():
        alias.merged_into_id = target_id
        session.add(alias)
    # The row stays as an alias: papers still hold this spelling, and it must keep leading to the person kept.
    source.merged_into_id = target_id
    session.add(source)
    target.confirmed = True
    session.add(target)
    session.commit()
