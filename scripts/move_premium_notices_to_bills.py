"""One-off (work-list B1b, Pedro 2026-11-04): the insurance premium notices become bills.

A premium notice is first of all a bill; it also shows whether the policy is still paid. So it is filed once,
under Bills, with its policy number, and the policy's card in Insurance lists it (domains/insurance/policies.py):
never counted twice. On 2026-11-04 six premium notices were Insurance papers (their kind the old "what it covers").
Each goes through the Hub's own re-file path, as "change" on its page would: it is taken off the policy's wiki page,
filed under Bills and read as a bill, and its bill line is matched with the bank line that paid it.

Run on the VPS, from the app folder, as the app's user, after the migration d2b6f8a1c437:
    python scripts/move_premium_notices_to_bills.py            # dry run: lists them
    python scripts/move_premium_notices_to_bills.py --apply    # moves and reads them (one model call each)
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine  # noqa: E402
from app.domains.fields import load_fields  # noqa: E402
from app.domains.financials.categories import FinancialsCategory  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.domain import Domain  # noqa: E402
from app.models.transaction import Transaction  # noqa: E402
from app.services.ingestion import Classification, refile_document  # noqa: E402


def premium_notices(session: Session) -> list[Document]:
    return [d for d in session.exec(select(Document).where(Document.domain == Domain.INSURANCE)).all()
            if load_fields(d).get("document_kind") == "premium_notice"]


async def run(session: Session, apply: bool) -> list[str]:
    report = []
    for document in premium_notices(session):
        number = load_fields(document).get("policy_number", "")
        report.append(f"document {document.id} {document.filename}: policy {number} -> Bills")
        if not apply:
            continue
        document = await refile_document(session, document, Classification(
            domain=Domain.FINANCIALS, paper_kind=FinancialsCategory.BILL.value, fields={"policy_number": number}))
        lines = session.exec(select(Transaction).where(Transaction.document_id == document.id)).all()
        read = ", ".join(f"{t.description} €{t.amount:,.2f}{' (settled by a bank line)' if t.settled_by_id else ''}"
                         for t in lines) or "no bill line read"
        report.append(f"   {document.status.value}: {read}{' — ' + document.failure_reason if document.failure_reason else ''}")
    return report


async def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    with Session(engine) as session:
        for line in await run(session, apply):
            print(line)
    print("applied" if apply else "dry run: nothing changed (add --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))
