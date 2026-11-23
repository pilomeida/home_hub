"""PLAN step 12b: guess again the papers waiting in the Inbox, through the same path as a new arrival (rules first,
then one AI call inside a High run). Their old guesses were made before Education, Identity, Vehicles and Pets existed
(2026-09), and carry no who, items, tags or filed name.

Run on the VPS, from the app folder, as the app's user (back up the database first):
    python scripts/guess_waiting_again.py                   dry run: lists the waiting papers; no AI call
    python scripts/guess_waiting_again.py --apply --limit 3 guesses the first 3 again, printing old -> new
    python scripts/guess_waiting_again.py --apply           all of them (a few cents in total)
A paper whose guess fails keeps its old guess (the reason goes to the Problems page). A second full run only spends
the same calls again; nothing is doubled."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.services import inbox_service  # noqa: E402


def waiting(session: Session) -> list:
    return inbox_service.pending_entries(session)


async def run(limit: int | None) -> None:
    from app.db import engine
    with Session(engine) as session:
        entries = waiting(session)[:limit] if limit else waiting(session)
        done = failed = 0
        for entry in entries:
            before = entry.suggestion_label or "no guess"
            try:
                item = await inbox_service.guess_again(session, entry.document.id)
            except inbox_service.GuessFailed as exc:
                failed += 1
                print(f"#{entry.document.id} {entry.document.filename}: kept {before} ({exc})")
                continue
            done += 1
            guess = inbox_service.guess_of(item)
            print(f"#{entry.document.id} {entry.document.filename}: {before} -> {inbox_service.suggestion_label(item)}"
                  f" | {guess.get('filed_name', '')}"
                  + (f" | who {guess['who']}" if guess.get("who") else "")
                  + (f" | items {', '.join(i['name'] + ' (' + str(i['room']) + ')' for i in guess['items'])}"
                     if guess.get("items") else ""))
        print(f"guessed again: {done}; kept the old guess: {failed}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    if not args.apply:
        from app.db import engine
        with Session(engine) as session:
            entries = waiting(session)
            for entry in entries:
                print(f"#{entry.document.id} {entry.document.filename}: now {entry.suggestion_label or 'no guess'}")
            print(f"dry run: {len(entries)} waiting paper(s) would be guessed again; run with --apply (and --limit N)")
        return
    asyncio.run(run(args.limit))


if __name__ == "__main__":
    main()
