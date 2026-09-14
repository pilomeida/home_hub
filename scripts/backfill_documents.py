"""One-off backfill: ingest every PDF in a source folder through the same
code path the web upload uses (save_upload -> dedup check -> Document ->
ingest_document). Idempotent via content-hash dedup, so re-running after a
partial failure just skips whatever already succeeded.

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
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import Transaction
from app.services.dedup import find_existing_document_by_hash
from app.services.pipeline import ingest_document
from app.services.storage import save_upload

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
            content = pdf_path.read_bytes()
            file_path, content_hash = save_upload(pdf_path.name, content)

            existing = find_existing_document_by_hash(session, content_hash)
            if existing is not None:
                print(f"SKIP (duplicate)     {pdf_path.name} -> document #{existing.id}")
                skipped += 1
                continue

            document = Document(
                filename=pdf_path.name,
                file_path=file_path,
                content_hash=content_hash,
                source=DocumentSource.MANUAL,
                status=DocumentStatus.PENDING,
                uploaded_by="backfill-script",
            )
            session.add(document)
            session.commit()
            session.refresh(document)

            document = await ingest_document(session, document)

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
