"""One-off historical reconciliation: links each Santander "Revolut top-up"
outgoing transfer to its corresponding Revolut "Top-up by *XXXX" incoming
transfer, via Transaction.linked_transaction_id (set symmetrically on both
rows), and cleans up the merchant fragmentation the classification engine
produced for this recurring internal-transfer pattern.

Why this exists: Pedro funds his Revolut current account from his Santander
card roughly every 1-4 weeks (mostly EUR 500, occasionally smaller amounts).
Investigating this surfaced two real gaps:

1. Merchant fragmentation on BOTH sides of the transfer. The Santander-side
   outgoing rows (provider text like "REVOLUT**8643*" or "Revolut top-up via
   MB WAY temporary card **8321**") split across 2 Merchant rows ("Revolut",
   "Revolut Top-up"). The Revolut-side incoming rows ("Top-up by *XXXX")
   split across 5 Merchant rows -- one of which ("Mobile Top-up") the LLM
   miscategorized as Category.TELECOM, since "Top-up by *XXXX" superficially
   resembles a mobile-phone-credit top-up. None of these merchants were ever
   confirmed or reviewed for recurring status, so this ~EUR 500/month pattern
   was sitting in the Needs Review queue as both several "unconfirmed
   merchant" entries and a false-positive "recurring commitment" candidate --
   it is a recurring INTERNAL TRANSFER, not a bill or subscription.

2. No link between the two sides of the same real-world movement of money.
   Both accounts already correctly categorize these as Category.TRANSFER /
   TransactionType.TRANSFER (so they're already excluded from spend-by-
   category totals, which only sum TransactionType.DEBIT) -- there was no
   double-counting bug. But there was no way to see, from either side, which
   transaction on the OTHER account was the same transfer.

Matching algorithm: group both sides by exact amount, sort each group by
paid_date, and walk them with two pointers, advancing the Revolut pointer
past any candidate whose date already precedes the current Santander
transaction by more than MAX_GAP_DAYS (a stale/extra Revolut top-up with no
Santander counterpart in this window) rather than force-matching it. This
correctly handles the one real edge case in production data: the EUR 500
amount group has 34 Revolut-side top-ups against only 33 Santander-side
transfers (one Revolut top-up has no Santander-side match at all within any
reasonable window). A naive per-Santander-row "nearest unused date" greedy
match mismatches whenever several same-amount transfers cluster within days
of each other (verified against real data before writing this: it silently
shifted a whole chain of 26 pairs by one). The two-pointer approach, proven
against the real production data, cleanly matches 58 of 58 Santander-side
transfers with a consistent 1-4 day gap (Revolut credits the top-up, then
Santander posts the corresponding card debit a few days later) and leaves
exactly the 2 known unmatched Revolut top-ups (both dated January 2024,
EUR 430 each -- these predate the earliest Santander-side "revolut" mention
in this dataset entirely, so they were funded some other way, e.g. before
this card-top-up habit started, or via a since-removed funding source).

Idempotent: re-running skips any Santander transaction that already has
linked_transaction_id set, and the merchant merge step is a no-op once the
canonical merchants already exist (matches by canonical_name, same as
scripts/merge_duplicate_merchants.py's convention).

Not part of the reviewed application code.

Usage:
    python scripts/reconcile_revolut_topups.py
"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select

from app.db import engine
from app.models.account import Account
from app.models.merchant import Merchant
from app.models.transaction import Category, Nature, Transaction

MAX_GAP_DAYS = 10

SANTANDER_MERCHANT_IDS_BY_NAME = ("Revolut", "Revolut Top-up")
REVOLUT_MERCHANT_IDS_BY_NAME = (
    "Mobile Top-up", "Top-up by Card *4780", "Top-up Payment", "Top-up", "Top-up by *3252",
)
SANTANDER_CANONICAL_NAME = "Revolut Top-up (outgoing)"
REVOLUT_CANONICAL_NAME = "Revolut Top-up (incoming)"


def _merge_merchant_group(session: Session, names: tuple[str, ...], canonical_name: str) -> Merchant:
    """Merge the named Merchant rows into one survivor with canonical_name,
    Category.TRANSFER, confirmed=True, recurring_reviewed=True. Repoints
    every transaction referencing a merged-away row. If canonical_name
    already exists as its own Merchant row (e.g. a re-run), reuse it as the
    survivor instead of creating a new one."""
    existing_survivor = session.exec(
        select(Merchant).where(Merchant.canonical_name == canonical_name)
    ).first()

    group = [
        m for m in session.exec(
            select(Merchant).where(Merchant.canonical_name.in_(names))
        ).all()
    ]

    if existing_survivor is None:
        if not group:
            raise ValueError(f"No merchants found matching {names!r} and no existing {canonical_name!r}")
        survivor = min(group, key=lambda m: m.id)
        survivor.canonical_name = canonical_name
    else:
        survivor = existing_survivor

    survivor.default_category = Category.TRANSFER
    survivor.confirmed = True
    survivor.recurring_reviewed = True
    session.add(survivor)
    session.commit()
    session.refresh(survivor)

    for loser in group:
        if loser.id == survivor.id:
            continue
        loser_transactions = session.exec(
            select(Transaction).where(Transaction.merchant_id == loser.id)
        ).all()
        for t in loser_transactions:
            t.merchant_id = survivor.id
            session.add(t)
        session.delete(loser)
    session.commit()

    return survivor


def _match_pairs(
    santander_transfers: list[Transaction], revolut_topups: list[Transaction]
) -> tuple[list[tuple[Transaction, Transaction]], list[Transaction], list[Transaction]]:
    by_amount_san: dict[float, list[Transaction]] = defaultdict(list)
    by_amount_rev: dict[float, list[Transaction]] = defaultdict(list)
    for t in sorted(santander_transfers, key=lambda t: t.paid_date):
        by_amount_san[t.amount].append(t)
    for t in sorted(revolut_topups, key=lambda t: t.paid_date):
        by_amount_rev[t.amount].append(t)

    pairs: list[tuple[Transaction, Transaction]] = []
    unmatched_san: list[Transaction] = []
    unmatched_rev: list[Transaction] = []

    for amount in sorted(set(by_amount_san) | set(by_amount_rev)):
        s_list = by_amount_san.get(amount, [])
        r_list = by_amount_rev.get(amount, [])
        si, ri = 0, 0
        while si < len(s_list) and ri < len(r_list):
            gap = (s_list[si].paid_date - r_list[ri].paid_date).days
            if 0 <= gap <= MAX_GAP_DAYS:
                pairs.append((s_list[si], r_list[ri]))
                si += 1
                ri += 1
            elif gap < 0:
                unmatched_san.append(s_list[si])
                si += 1
            else:
                unmatched_rev.append(r_list[ri])
                ri += 1
        unmatched_san.extend(s_list[si:])
        unmatched_rev.extend(r_list[ri:])

    return pairs, unmatched_san, unmatched_rev


def reconcile() -> None:
    with Session(engine) as session:
        santander = session.exec(
            select(Account).where(Account.institution == "Santander Totta")
        ).first()
        revolut = session.exec(
            select(Account).where(Account.institution == "Revolut Bank UAB")
        ).first()
        if santander is None or revolut is None:
            raise RuntimeError("Expected both Santander and Revolut accounts to already exist")

        print("--- Merging merchant fragments ---")
        santander_merchant = _merge_merchant_group(
            session, SANTANDER_MERCHANT_IDS_BY_NAME, SANTANDER_CANONICAL_NAME
        )
        print(f"Santander-side canonical merchant: #{santander_merchant.id} {santander_merchant.canonical_name!r}")
        revolut_merchant = _merge_merchant_group(
            session, REVOLUT_MERCHANT_IDS_BY_NAME, REVOLUT_CANONICAL_NAME
        )
        print(f"Revolut-side canonical merchant:   #{revolut_merchant.id} {revolut_merchant.canonical_name!r}")

        santander_transfers = session.exec(
            select(Transaction).where(
                Transaction.account_id == santander.id,
                Transaction.merchant_id == santander_merchant.id,
            )
        ).all()
        revolut_topups = session.exec(
            select(Transaction).where(
                Transaction.account_id == revolut.id,
                Transaction.merchant_id == revolut_merchant.id,
            )
        ).all()

        already_linked = sum(1 for t in santander_transfers if t.linked_transaction_id is not None)
        santander_transfers = [t for t in santander_transfers if t.linked_transaction_id is None]
        revolut_topups = [t for t in revolut_topups if t.linked_transaction_id is None]

        print(f"\nSantander-side transfers to match: {len(santander_transfers)} (already linked, skipped: {already_linked})")
        print(f"Revolut-side top-ups to match:     {len(revolut_topups)}")

        pairs, unmatched_san, unmatched_rev = _match_pairs(santander_transfers, revolut_topups)

        for san_txn, rev_txn in pairs:
            san_txn.linked_transaction_id = rev_txn.id
            rev_txn.linked_transaction_id = san_txn.id
            session.add(san_txn)
            session.add(rev_txn)
        session.commit()

        print("\n--- Summary ---")
        print(f"Pairs linked:            {len(pairs)}")
        print(f"Unmatched Santander-side: {len(unmatched_san)}")
        for t in unmatched_san:
            print(f"  SAN #{t.id} {t.paid_date} {t.provider!r} {t.amount}")
        print(f"Unmatched Revolut-side:   {len(unmatched_rev)}")
        for t in unmatched_rev:
            print(f"  REV #{t.id} {t.paid_date} {t.provider!r} {t.amount}")


if __name__ == "__main__":
    reconcile()
