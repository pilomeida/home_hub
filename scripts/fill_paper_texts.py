"""One-off (2026-11-19, PLAN step 1a): give the papers already on file their text and build the search index.
New papers get their text when they are filed; the hourly "Paper text" job reads the rest.

Run on the VPS, from the app folder, as the app's user, after the migration:
    python scripts/fill_paper_texts.py                 # dry run: how many have a text layer, how many need the AI
    python scripts/fill_paper_texts.py --apply         # keep the PDFs' own text and build the index (no AI)
    python scripts/fill_paper_texts.py --apply --ai 20 # also let the AI read up to 20 papers that have no text
Only `paper_texts` and the `paper_search` index are written. Safe to run again."""

import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.privacy.settings import configured_tier  # noqa: E402
from app.services.document_input import is_model_readable  # noqa: E402
from app.services.paper_search import (  # noqa: E402
    missing_text_documents, pdf_text_of, read_missing_texts, rebuild_index,
)


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    ai = int(argv[argv.index("--ai") + 1]) if "--ai" in argv else 0
    with Session(engine) as session:
        todo = missing_text_documents(session)
        has_text, need_ai, unreadable = 0, Counter(), 0
        for doc in todo:
            if pdf_text_of(doc.file_path):
                has_text += 1
            elif is_model_readable(doc.file_path) and Path(doc.file_path).exists():
                need_ai[configured_tier(session, doc.category, doc.paper_kind) or "no level"] += 1
            else:
                unreadable += 1
        print(f"{len(todo)} filed papers have no text yet: {has_text} carry their own text, "
              f"{sum(need_ai.values())} need the AI ({dict(need_ai)}), {unreadable} cannot be read (video, CSV, missing)")
        if not apply:
            print("dry run: nothing changed (add --apply)")
            return 0
        run = asyncio.run(read_missing_texts(session, batch=ai))
        print(f"text kept for {run.read}, no text {run.no_text}, AI failed {run.failed}, "
              f"waiting for a level {run.waiting_for_level}, left for the job {run.left}")
        print(f"search index rebuilt: {rebuild_index(session)} papers")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
