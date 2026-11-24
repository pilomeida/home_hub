"""PLAN step 12e: the papers already filed get names by the same rule as new ones (`date · kind · what it is · who
or item`, app/services/paper_names.py). A name is built from what the Hub already holds of the paper (each area says
how, `CategorySpec.name_parts`); the AI reads the paper's stored text only where that holds no "what it is", at the
paper's own privacy level (D16). The name it came in with is kept and shown as "came in as"; the file never changes.

Run on the VPS, from the app folder, as the app's user (back up the database first):
    python scripts/rename_filed_papers.py                    dry run: old -> new for every paper; no AI call
    python scripts/rename_filed_papers.py --apply --limit 5  renames the first 5
    python scripts/rename_filed_papers.py --apply            all of them (the AI only for those marked "AI")
A paper renamed before (by this script or on approval) is left as it is, so a second run changes nothing. A paper
for which no "what it is" can be found keeps its name, and is listed."""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.models.document import Document, DocumentStatus  # noqa: E402
from app.services import paper_filing  # noqa: E402


def to_rename(session: Session) -> list[Document]:
    """Filed papers with a file that were never renamed."""
    return list(session.exec(select(Document).where(
        Document.category.is_not(None), Document.status.in_([DocumentStatus.PROCESSED, DocumentStatus.NEEDS_ATTENTION]),
        Document.original_filename.is_(None), Document.file_path != "").order_by(Document.id)))


async def run(session: Session, *, apply: bool, limit: int | None, gateway=None) -> dict:
    from app.privacy.settings import TierHeld
    from app.services.paper_search import index_document
    counts = {"papers": 0, "renamed": 0, "same": 0, "kept": 0, "ai": 0, "waiting_for_level": 0}
    papers = to_rename(session)
    for document in papers[:limit] if limit else papers:
        counts["papers"] += 1
        parts = paper_filing.known_parts(session, document)
        source = "known"
        if not (parts.get("what") or "").strip():
            source = "AI"
            if apply:
                counts["ai"] += 1
                try:
                    read = await paper_filing.ai_parts(session, document, gateway)
                    parts["what"] = read["what"]
                    parts["paper_date"] = parts.get("paper_date") or read["paper_date"]
                except TierHeld:
                    counts["waiting_for_level"] += 1
                    print(f"#{document.id} {document.filename}: kept (its kind has no privacy level yet)")
                    continue
        if apply and not (parts.get("what") or "").strip():
            counts["kept"] += 1
            print(f"#{document.id} {document.filename}: kept (nothing says what it is)")
            continue
        name = paper_filing.name_from(session, document, parts)
        shown = name + paper_filing.extension(document) if parts.get("what") else "(the AI says what it is)"
        print(f"#{document.id} {document.filename} -> {shown} [{source}]")
        if not apply:
            continue
        old = document.filename
        if paper_filing.rename(document, name):
            session.add(document)
            session.commit()
            index_document(session, document)
            counts["renamed"] += 1
        else:
            counts["same"] += 1
            print(f"#{document.id} {old}: already so named")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    from app.db import engine
    with Session(engine) as session:
        counts = asyncio.run(run(session, apply=args.apply, limit=args.limit))
    if args.apply:
        print(f"renamed {counts['renamed']}; already so named {counts['same']}; kept {counts['kept']}; "
              f"AI calls {counts['ai']}; waiting for a level {counts['waiting_for_level']}")
    else:
        print(f"dry run: {counts['papers']} paper(s) would be renamed; run with --apply (and --limit N)")


if __name__ == "__main__":
    main()
