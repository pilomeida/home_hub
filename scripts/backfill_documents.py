"""One-off backfill: ingest every PDF in a source folder through the same
generic ingestion core the web upload uses (app.services.ingestion.ingest:
content-hash dedup -> store -> Financials handler). Idempotent via
content-hash dedup, so re-running after a partial failure just skips
whatever already succeeded.

Not part of the reviewed application code (see the bank-statement-ingestion
spec's "Out of Scope" section) — a personal utility for the historical
Santander/Revolut/bills backfill.

Usage:
    python scripts/backfill_documents.py ["Bills & Bank Statements"]
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select

from app.db import engine
from app.models.document import DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.transaction import Transaction
from app.services.ingestion import Classification, IncomingFile, ingest

DEFAULT_SOURCE_DIR = Path(__file__).resolve().parent.parent / "Bills & Bank Statements"


async def backfill(source_dir: Path) -> None:
    pdf_files = sorted(source_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDF files found in {source_dir}")
        return

    print(f"Found {len(pdf_files)} PDF file(s) in {source_dir}\n")

    created = 0
    skipped = 0
    needs_attention = 0

    with Session(engine) as session:
        for pdf_path in pdf_files:
            result = await ingest(
                session,
                IncomingFile(
                    filename=pdf_path.name, content=pdf_path.read_bytes(),
                    source=DocumentSource.MANUAL, uploaded_by="backfill-script",
                ),
                Classification(domain=Domain.FINANCIALS),
            )
            document = result.document
            if result.duplicate:
                print(f"SKIP (duplicate)     {pdf_path.name} -> document #{document.id}")
                skipped += 1
                continue

            if document.status == DocumentStatus.PROCESSED:
                txn_count = len(
                    session.exec(
                        select(Transaction).where(Transaction.document_id == document.id)
                    ).all()
                )
                print(
                    f"OK ({txn_count} txn{'s' if txn_count != 1 else ''})       "
                    f"{pdf_path.name} -> document #{document.id}"
                )
                created += 1
            else:
                print(
                    f"NEEDS_ATTENTION      {pdf_path.name} -> document #{document.id}: "
                    f"{document.failure_reason}"
                )
                needs_attention += 1

    print("\n--- Summary ---")
    print(f"Processed:       {created}")
    print(f"Skipped (dup):   {skipped}")
    print(f"Needs attention: {needs_attention}")
    print(f"Total files:     {len(pdf_files)}")


if __name__ == "__main__":
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE_DIR
    asyncio.run(backfill(source))
