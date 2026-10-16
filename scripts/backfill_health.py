"""Bulk-ingest Pedro's existing health reports. Each file goes through the same path as a Telegram or email
paper: the Inbox (recognised by rule, or read once privately to decide what it is, decision D20). With
--auto-approve, a paper the Hub is confident is Health is approved at once, which reads it (always at High),
fills the Health tab and proposes areas, conditions and people. Anything else waits in the Inbox.
Idempotent: a file already received is skipped by its content hash.

Usage:
    python scripts/backfill_health.py <folder> [--auto-approve]
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session

from app.db import engine
from app.models.document import DocumentSource
from app.services.domain_classifier import suggest_domain_and_category
from app.services.inbox_service import approve, is_confident, receive_document
from app.services.ingestion import IncomingFile

_SKIP = {".txt", ".md", ".ds_store"}


async def backfill(session: Session, folder: Path, auto_approve: bool,
                   classify=suggest_domain_and_category, approve=approve) -> dict[str, int]:
    counts = {"received": 0, "duplicates": 0, "approved": 0, "waiting": 0, "failed": 0}
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() not in _SKIP):
        try:
            receipt = await receive_document(
                session, IncomingFile(path.name, path.read_bytes(), DocumentSource.MANUAL, "backfill"),
                context_text=None, external_ref=f"backfill:{path}", classify=classify)
        except Exception as exc:
            print(f"FAILED {path.name}: {exc}")
            counts["failed"] += 1
            continue
        if receipt.duplicate:
            counts["duplicates"] += 1
            continue
        counts["received"] += 1
        item = receipt.inbox_item
        if auto_approve and item is not None and is_confident(item) and item.suggested_domain == "health":
            try:
                await approve(session, receipt.document.id, domain_value="health", category=item.suggested_category,
                              fields={}, reviewed_by="backfill")
                counts["approved"] += 1
                continue
            except Exception as exc:
                print(f"FAILED to file {path.name}: {exc}")
                counts["failed"] += 1
                continue
        counts["waiting"] += 1
    return counts


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    folder = Path(sys.argv[1])
    with Session(engine) as session:
        counts = asyncio.run(backfill(session, folder, auto_approve="--auto-approve" in sys.argv))
    print(counts)
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
