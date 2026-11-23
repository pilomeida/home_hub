"""PLAN step 12a: seed the family (Pedro, Rute, Matias, Vicente) and the 17 rooms of the house as subjects, from the
lists in app/services/household.py (Pedro, 2026-11-23). A person already known by the same name is marked as family
rather than added twice. A second run changes nothing.

Run on the VPS, from the app folder, as the app's user (back up the database first):
    python scripts/seed_household.py            dry run: what it would add or mark; changes nothing
    python scripts/seed_household.py --apply    adds and marks them, then prints the lists"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.models.subject import SubjectKind  # noqa: E402
from app.services import household, subjects  # noqa: E402


def plan(session: Session) -> list[str]:
    out = []
    for name in household.FAMILY:
        person = subjects.find(session, SubjectKind.PERSON, name)
        if person is None:
            out.append(f"add person {name} (family)")
        elif not (person.household and person.confirmed):
            out.append(f"mark person {person.name} (#{person.id}) as family")
    out += [f"add room {name}" for name in household.ROOMS if subjects.find(session, SubjectKind.ROOM, name) is None]
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with Session(engine) as session:
        steps = plan(session)
        print("\n".join(steps) or "nothing to do")
        if not args.apply:
            print(f"dry run: {len(steps)} change(s); run with --apply to make them")
            return
        print("applied:", household.ensure(session))
        print("family:", ", ".join(s.name for s in household.family(session)))
        print("rooms:", ", ".join(s.name for s in household.rooms(session)))


if __name__ == "__main__":
    main()
