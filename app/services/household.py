"""The family and the rooms, defined once as subjects (PLAN step 12a, S5; lists from Pedro, 2026-11-23).

The family: people subjects with `household` set. The rooms: subjects of kind ROOM. House's room field suggests from
the rooms, and an item filed in a room not on the list adds that room (12c shows it as "New room" first)."""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from app.models.subject import Subject, SubjectKind
from app.services import subjects

FAMILY = ("Pedro", "Rute", "Matias", "Vicente")
ROOMS = ("Kitchen", "Living/dining room", "Hall", "Office", "Suite bedroom", "Suite restroom", "Matias' bedroom",
         "Vicente's bedroom", "Social restroom", "1st floor restroom", "Solarium", "Store room (annex)",
         "Outside restroom", "Porch", "Garden", "Parking", "Whole house")


def ensure(session: Session) -> dict[str, int]:
    """Seed the family and the rooms; a second run changes nothing. Returns how many were added or marked."""
    people = rooms = 0
    for name in FAMILY:
        person = subjects.register_person(session, name)
        if not (person.household and person.confirmed):
            person.household, person.confirmed = True, True
            session.add(person)
            people += 1
    for name in ROOMS:
        if subjects.find(session, SubjectKind.ROOM, name) is None:
            subjects.register(session, SubjectKind.ROOM, name).confirmed = True
            rooms += 1
    session.commit()
    return {"people": people, "rooms": rooms}


def family(session: Session) -> list[Subject]:
    return list(session.exec(select(Subject).where(Subject.kind == SubjectKind.PERSON, Subject.household.is_(True),
                                                   Subject.merged_into_id.is_(None)).order_by(Subject.id)).all())


def rooms(session: Session) -> list[Subject]:
    return list(session.exec(select(Subject).where(Subject.kind == SubjectKind.ROOM, Subject.merged_into_id.is_(None))
                             .order_by(Subject.id)).all())


def room_options(session: Session) -> list[tuple[str, str]]:
    return [(s.name, s.name) for s in rooms(session)]


def add_room(session: Session, name: Optional[str]) -> Optional[Subject]:
    """A room an item was filed in joins the list (find-or-create by name, accents and case ignored)."""
    name = " ".join((name or "").split())
    if not name:
        return None
    found = subjects.find(session, SubjectKind.ROOM, name)
    if found is not None:
        return found
    room = subjects.register(session, SubjectKind.ROOM, name)
    room.confirmed = True                     # Pedro filed the item there
    session.add(room)
    return room
