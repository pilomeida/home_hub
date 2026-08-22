"""One-off data cleanup: merges Merchant rows that share the same
canonical_name (case-insensitive) into one survivor row, repointing every
Transaction that referenced a merged-away row.

Why this exists: normalize_provider()'s rules-tier regex only strips a
specific set of known trailing store-code/location-word patterns (see
app/services/classification_engine.py). Real provider strings the rules
tier doesn't recognize (e.g. "CCR-MODELO HIPER", "COMPRA MODELO HIPER")
fall through to the LLM fallback independently each time, and even though
the LLM correctly proposes the same canonical_name for all of them, each
gets its own normalized_key and therefore its own Merchant row -- the same
real-world merchant ends up split across several rows. Running the full
historical backfill (scripts/backfill_transaction_classification.py)
against real data surfaced this: 512 of 1,468 merchant rows were exact
case-insensitive canonical_name duplicates of another row.

This script only merges EXACT (case-insensitive, whitespace-trimmed)
canonical_name matches -- a conservative, safe win. It deliberately does
NOT attempt fuzzy/near-match merging (e.g. "Mortgage Payment" vs "Mortgage
Payment - Housing Credit", which a human can tell are the same merchant but
an exact-match script correctly leaves alone) -- that's a harder problem
with real false-merge risk, out of scope here.

For each duplicate group: the row with the most linked transactions is
kept as the survivor (ties broken by lowest id, i.e. the oldest row).
Every other row's transactions are repointed to the survivor via
merchant_id, the survivor's confirmed/recurring_reviewed flags become the
OR of every row in the group (so nothing already reviewed re-enters the
Needs Review queue as if it were new), and the now-unreferenced rows are
deleted. default_category/default_nature are left as whatever the
survivor already had -- since every row in a group was independently
resolved by the LLM to the same canonical_name, their category/nature are
expected to already agree in practice.

Idempotent by construction: after one run, every remaining merchant has a
distinct case-insensitive canonical_name, so a second run finds no
duplicate groups and changes nothing.

Not part of the reviewed application code (see the classification-engine
spec's "Backfill" section and this fix's own PR discussion for context).

Usage:
    python scripts/merge_duplicate_merchants.py
"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select

from app.db import engine
from app.models.merchant import Merchant
from app.models.transaction import Transaction


def merge_duplicate_merchants() -> None:
    with Session(engine) as session:
        merchants = session.exec(select(Merchant)).all()

        groups: dict[str, list[Merchant]] = defaultdict(list)
        for merchant in merchants:
            key = merchant.canonical_name.strip().lower()
            groups[key].append(merchant)

        duplicate_groups = {key: group for key, group in groups.items() if len(group) > 1}

        merged_rows = 0
        repointed_transactions = 0
        errors = []

        for key, group in duplicate_groups.items():
            try:
                transaction_counts = {
                    merchant.id: len(
                        session.exec(
                            select(Transaction).where(Transaction.merchant_id == merchant.id)
                        ).all()
                    )
                    for merchant in group
                }
                survivor = max(group, key=lambda m: (transaction_counts[m.id], -m.id))
                losers = [m for m in group if m.id != survivor.id]

                for loser in losers:
                    loser_transactions = session.exec(
                        select(Transaction).where(Transaction.merchant_id == loser.id)
                    ).all()
                    for transaction in loser_transactions:
                        transaction.merchant_id = survivor.id
                        session.add(transaction)
                        repointed_transactions += 1

                    survivor.confirmed = survivor.confirmed or loser.confirmed
                    survivor.recurring_reviewed = survivor.recurring_reviewed or loser.recurring_reviewed

                    session.delete(loser)
                    merged_rows += 1

                session.add(survivor)
                session.commit()
            except Exception as exc:
                session.rollback()
                errors.append(f"merchant group {key!r}: {exc}")

        print(f"Duplicate groups found: {len(duplicate_groups)}")
        print(f"Merchant rows merged away: {merged_rows}")
        print(f"Transactions repointed:    {repointed_transactions}")
        print(f"Errors:                    {len(errors)}")
        for e in errors:
            print(f"  - {e}")


if __name__ == "__main__":
    merge_duplicate_merchants()
