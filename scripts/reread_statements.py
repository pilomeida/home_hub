"""One-off (2026-11-19, PLAN step 4c): read again the statements that failed before (documents 2, 22, 33, 51).
They are before the 2025-10-10 baseline, so their lines are history and spending analysis only: no live total moves.
Only a statement that holds no lines yet is read; a statement for days the feed or another statement holds shows
them as overlap (app/services/feed_overlap.py).

Run on the VPS, from the app folder, as the app's user:
    python scripts/reread_statements.py 2 22 33 51            # dry run: what would be read
    python scripts/reread_statements.py 2 22 33 51 --apply
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, func, select  # noqa: E402

from app.categories.registry import get_spec  # noqa: E402
from app.db import engine  # noqa: E402
from app.models.document import Document, DocumentStatus  # noqa: E402
from app.models.transaction import StatementCheck, Transaction  # noqa: E402
from app.privacy.scope import privacy_scope  # noqa: E402


async def _read(session: Session, doc: Document) -> Document:
    with privacy_scope(session, doc.category, doc.paper_kind):
        return await get_spec(doc.category).handler.process(session, doc)


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    ids = [int(a) for a in argv[1:] if a.isdigit()]
    with Session(engine) as session:
        for doc_id in ids:
            doc = session.get(Document, doc_id)
            lines = session.exec(select(func.count()).select_from(Transaction).where(Transaction.document_id == doc_id)).one()
            if doc is None or doc.paper_kind != "statement" or lines:
                print(f"{doc_id}: skipped ({'missing' if doc is None else f'{doc.paper_kind}, {lines} lines already'})")
                continue
            print(f"{doc_id} {doc.filename}: {doc.status.value}, would be read again")
            if not apply:
                continue
            doc = asyncio.run(_read(session, doc))
            n = session.exec(select(func.count()).select_from(Transaction).where(Transaction.document_id == doc_id)).one()
            check = session.exec(select(StatementCheck).where(StatementCheck.document_id == doc_id)).first()
            overlap = session.exec(select(func.count()).select_from(Transaction).where(
                Transaction.document_id == doc_id, Transaction.settled_by_id.is_not(None))).one()
            print(f"   -> {doc.status.value}: {n} lines ({overlap} already held elsewhere, shown as overlap), "
                  f"add-up {check.status if check else 'not checked'}"
                  + (f"; reason: {doc.failure_reason[:160]}" if doc.status != DocumentStatus.PROCESSED else ""))
        if not apply:
            print("dry run: nothing changed (add --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
