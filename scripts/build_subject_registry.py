"""One-off (work-list B4, 2026-11-05): register every name the Hub already holds in the subjects registry, and
propose the matches for the family to answer on the Inbox ("Same person?"). Nothing is joined here.

Run on the VPS, from the app folder, as the app's user, after migration f7b3d9e1a524:
    python scripts/build_subject_registry.py            # dry run: what it would register and propose
    python scripts/build_subject_registry.py --apply
Safe to run again: what is registered stays, nothing is asked twice."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.services.subjects import build_registry, pending  # noqa: E402


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    with Session(engine) as session:
        print(build_registry(session, commit=apply))
        for p in pending(session):
            print(f"  same person? {p.one}  /  {p.other}")
        if not apply:
            session.rollback()
            print("dry run: nothing changed (add --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
