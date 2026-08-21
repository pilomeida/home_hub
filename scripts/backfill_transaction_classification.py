"""One-off historical backfill: assigns the single real Account to every
existing Document, then runs classify_transaction() across every existing
Transaction, populating merchant_id/account_id/nature and surfacing
recurring/debt candidates for later review via the Needs Review UI.

Idempotent via merchant_id IS NULL — safe to re-run if interrupted partway.

Not part of the reviewed application code (see the classification-engine
spec's "Backfill" section).

Usage:
    python scripts/backfill_transaction_classification.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select

from app.db import engine
from app.models.account import Account
from app.models.document import Document
from app.models.transaction import Transaction
from app.services.classification_engine import classify_transaction


async def backfill() -> None:
    with Session(engine) as session:
        account = session.exec(select(Account)).first()
        if account is None:
            print("No Account exists yet — create one before running this backfill.")
            return

        documents = session.exec(
            select(Document).where(Document.account_id.is_(None))
        ).all()
        for document in documents:
            document.account_id = account.id
            session.add(document)
        session.commit()
        print(f"Assigned account {account.id!r} ({account.name}) to {len(documents)} documents.")

        transactions = session.exec(
            select(Transaction).where(Transaction.merchant_id.is_(None))
        ).all()
        classified = 0
        errors = []
        for transaction in transactions:
            try:
                await classify_transaction(session, transaction)
                session.commit()
                classified += 1
            except Exception as exc:
                session.rollback()
                errors.append(f"transaction #{transaction.id} ({transaction.provider!r}): {exc}")

        print("\n--- Summary ---")
        print(f"Classified:      {classified}")
        print(f"Errors:          {len(errors)}")
        for e in errors:
            print(f"  - {e}")


if __name__ == "__main__":
    asyncio.run(backfill())
