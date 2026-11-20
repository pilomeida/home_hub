"""One-off (PLAN step 8b, 2026-11-20): Other papers whose title or summary says contract or letter move to those kinds
(app/categories/other/refile.py has the words). Nothing else changes; a person can re-file any paper from its page.

Run on the VPS, from the app folder, as the app's user (back up the database first):
    python scripts/refile_other_papers.py            # dry run: what it would do
    python scripts/refile_other_papers.py --apply
Safe to run again."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.categories.other import refile  # noqa: E402
from app.db import engine  # noqa: E402


def main(argv: list[str]) -> int:
    with Session(engine) as session:
        moves = refile.apply(session) if "--apply" in argv else refile.plan(session)
        for m in moves:
            print(f"document {m.document_id}: {m.title!r} -> {m.to}")
        print(f"{len(moves)} papers: " + ("re-filed" if "--apply" in argv else "dry run, nothing changed (add --apply)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
