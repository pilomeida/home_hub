"""One-off historical import: ingests Pedro's own Revolut "Account
(Current Account)" transactions from Jan 2024 onward, out of the same
221-page combined statement PDF that also covers 4 additional family
sub-products (Matias's and Vicente's current accounts and pockets) --
NOT handled by this script, scoped out deliberately.

Why chunked: this one product alone has ~1,131 transactions since Jan
2024 -- far beyond what a single extract_statement_transactions() call
can return (the existing 16384 max_tokens output limit). This script
slices the source PDF into per-calendar-month page ranges (hardcoded
below, derived by inspecting the actual PDF text layout for this specific
statement -- not a generic PDF-statement chunker) and calls the existing
extraction pipeline once per month. Page ranges may overlap by one page
at month boundaries (a page can contain the tail of one month and the
head of the next); this is handled by filtering each month's extracted
transactions to their own calendar month via the transaction_date Claude
already returns, so a page appearing in two adjacent chunks never causes
a duplicate transaction, only a discarded out-of-range one.

Requires pypdf (pip install pypdf) -- not an application dependency, only
needed to run this script; safe to uninstall afterward.

Idempotent per month: skips any (year, month) that already has a
Document row from a previous run of this exact script, so it's safe to
re-run after a partial failure.

Does NOT classify the new transactions (merchant_id/nature) -- run
scripts/backfill_transaction_classification.py again afterward, which
already picks up any transaction with merchant_id IS NULL.

Not part of the reviewed application code.

Usage:
    python scripts/backfill_revolut_pedro_account.py
"""

import asyncio
import sys
from datetime import date
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pypdf import PdfReader, PdfWriter
from sqlmodel import Session, select

from app.db import engine
from app.models.account import Account, AccountType
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.transaction import Transaction, TransactionType
from app.services.categorization import normalize_category
from app.services.extraction import extract_statement_transactions
from app.services.storage import save_upload

# Resolved relative to this script's expected run location (the app's
# working directory, /srv/home-hub/app) -- adjust if run elsewhere.
SOURCE_PDF = Path("app/static/documents/a5d07abed2f14741b348548c78436522.pdf")

# (year, month) -> (first_page, last_page), 1-indexed inclusive, derived
# from this specific statement's text layout for Section 1 only (Pedro's
# own "Account (Current Account)", pages 34-95 of 221).
MONTH_PAGE_RANGES = {
    (2024, 1): (34, 37), (2024, 2): (37, 38), (2024, 3): (38, 40),
    (2024, 4): (40, 43), (2024, 5): (43, 45), (2024, 6): (45, 47),
    (2024, 7): (47, 48), (2024, 8): (48, 50), (2024, 9): (50, 52),
    (2024, 10): (52, 54), (2024, 11): (54, 55), (2024, 12): (55, 58),
    (2025, 1): (58, 61), (2025, 2): (61, 62), (2025, 3): (62, 63),
    (2025, 4): (63, 64), (2025, 5): (65, 68), (2025, 6): (69, 71),
    (2025, 7): (71, 74), (2025, 8): (74, 76), (2025, 9): (76, 78),
    (2025, 10): (78, 80), (2025, 11): (80, 82), (2025, 12): (82, 85),
    (2026, 1): (85, 86), (2026, 2): (86, 86), (2026, 3): (87, 88),
    (2026, 4): (88, 91), (2026, 5): (91, 93), (2026, 6): (93, 94),
    (2026, 7): (94, 95), (2026, 8): (95, 95),
}


def _extract_page_range_pdf(source_path: Path, first_page: int, last_page: int) -> bytes:
    reader = PdfReader(str(source_path))
    writer = PdfWriter()
    for i in range(first_page - 1, last_page):
        writer.add_page(reader.pages[i])
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


async def backfill() -> None:
    with Session(engine) as session:
        account = session.exec(
            select(Account).where(Account.institution == "Revolut Bank UAB")
        ).first()
        if account is None:
            account = Account(
                name="Revolut Current Account",
                institution="Revolut Bank UAB",
                currency="EUR",
                account_type=AccountType.CHECKING,
                identifier="LT703250030112861848",
            )
            session.add(account)
            session.commit()
            session.refresh(account)
            print(f"Created account #{account.id}: {account.name!r} ({account.institution!r})")
        else:
            print(f"Reusing existing account #{account.id}: {account.name!r}")

        created_documents = 0
        created_transactions = 0
        errors = []

        for (year, month), (first_page, last_page) in sorted(MONTH_PAGE_RANGES.items()):
            filename = f"revolut-pedro-{year}-{month:02d}.pdf"
            existing = session.exec(select(Document).where(Document.filename == filename)).first()
            if existing is not None:
                print(f"SKIP {year}-{month:02d} (already imported as document #{existing.id})")
                continue

            document = None
            try:
                chunk_bytes = _extract_page_range_pdf(SOURCE_PDF, first_page, last_page)
                file_path, content_hash = save_upload(filename, chunk_bytes)

                document = Document(
                    filename=filename, file_path=file_path, content_hash=content_hash,
                    source=DocumentSource.MANUAL, status=DocumentStatus.PENDING,
                    doc_type="statement", account_id=account.id,
                    uploaded_by="revolut-chunked-backfill",
                    domain=Domain.FINANCIALS,
                )
                session.add(document)
                session.commit()
                session.refresh(document)
                created_documents += 1

                extracted = await extract_statement_transactions(file_path)
                month_start = date(year, month, 1)
                month_end = date(year + (month == 12), (month % 12) + 1, 1)

                chunk_transaction_count = 0
                for item in extracted.transactions:
                    if not (month_start <= item.transaction_date < month_end):
                        continue
                    transaction = Transaction(
                        document_id=document.id,
                        provider=item.description,
                        category=normalize_category(item.category_hint),
                        transaction_type=TransactionType(item.transaction_type.strip().lower()),
                        amount=abs(item.amount),
                        currency=item.currency,
                        paid_date=item.transaction_date,
                        statement_period=f"{year}-{month:02d}",
                        account_id=account.id,
                    )
                    session.add(transaction)
                    chunk_transaction_count += 1
                session.commit()
                created_transactions += chunk_transaction_count

                document.status = DocumentStatus.PROCESSED
                session.add(document)
                session.commit()
                print(f"OK   {year}-{month:02d} (pages {first_page}-{last_page}) -> "
                      f"document #{document.id}, {chunk_transaction_count} transactions")
            except Exception as exc:
                session.rollback()
                if document is not None and document.id is not None:
                    session.refresh(document)
                    document.status = DocumentStatus.NEEDS_ATTENTION
                    document.failure_reason = str(exc)
                    session.add(document)
                    session.commit()
                errors.append(f"{year}-{month:02d}: {exc}")
                print(f"ERROR {year}-{month:02d}: {exc}")

        print("\n--- Summary ---")
        print(f"Documents created:    {created_documents}")
        print(f"Transactions created: {created_transactions}")
        print(f"Errors:               {len(errors)}")
        for e in errors:
            print(f"  - {e}")


if __name__ == "__main__":
    asyncio.run(backfill())
