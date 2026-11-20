"""One-off (PLAN step 6c, 2026-11-20): English names for the Health areas and wiki topics already on file; the
Portuguese names stay as aliases (app/services/english_names.py has the lists).

Run on the VPS, from the app folder, as the app's user, after the migration (back up the database first):
    python scripts/english_names.py            # dry run: what it would do
    python scripts/english_names.py --apply
Safe to run again."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.services import english_names  # noqa: E402


def main(argv: list[str]) -> int:
    with Session(engine) as session:
        todo = english_names.apply(session) if "--apply" in argv else english_names.plan(session)
        for kind, pt, en in todo.merges:
            print(f"{kind}: {pt!r} -> {en!r} (the Portuguese stays an alias)")
        for old, new in todo.renames:
            print(f"wiki topic: {old!r} -> {new!r} (the old title stays an alias)")
        print(f"{len(todo.merges)} areas/conditions, {len(todo.renames)} topics: "
              + ("applied" if "--apply" in argv else "dry run, nothing changed (add --apply)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
