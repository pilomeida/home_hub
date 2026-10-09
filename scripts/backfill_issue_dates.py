"""Fill Transaction.issue_date for bills filed before it was extracted: re-read each bill document and take
ONLY its issue date (nothing else changes). One model call per document, through the llmsel gateway.
Dry run unless --apply. Then run scripts/reconcile_documents.py.

    python scripts/backfill_issue_dates.py [--apply]
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.transaction import Transaction  # noqa: E402
from app.services.extraction import extract_bill  # noqa: E402


async def main() -> None:
    apply = "--apply" in sys.argv
    with Session(engine) as session:
        rows = session.exec(
            select(Transaction, Document).join(Document, Document.id == Transaction.document_id)
            .where(Transaction.issue_date.is_(None), Transaction.settled_by_id.is_(None))
            .where((Document.category.is_(None)) | (Document.category == "bill"))
        ).all()
        done = 0
        for txn, document in rows:
            try:
                extracted = await extract_bill(document.file_path)
            except Exception as exc:  # noqa: BLE001
                print(f"  document {document.id} {document.filename}: failed ({exc})")
                continue
            print(f"  document {document.id} {document.filename}: issued {extracted.issue_date}")
            if apply and extracted.issue_date:
                txn.issue_date = extracted.issue_date
                session.add(txn)
                done += 1
        if apply:
            session.commit()
        print(f"{'filled' if apply else 'would fill'}: {done if apply else sum(1 for _ in rows)} of {len(rows)}")


if __name__ == "__main__":
    asyncio.run(main())
