"""Link Pedro's Revolut top-ups to the Santander transfers that fund them.

A top-up shows up twice: a Santander debit ("... Revolut ...") a few days after
the Revolut credit ("Top-Up by *1234"). Left alone, the debit counts as spend and
the credit as income. Pairing them (same amount to the cent, Revolut first, at
most _MAX_GAP_DAYS apart, one-to-one, oldest first) and typing both as TRANSFER
matches how the historical statement rows were reconciled
(scripts/reconcile_revolut_topups.py)."""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlmodel import Session, select

from app.models.account import Account
from app.models.transaction import Category, Transaction, TransactionType

logger = logging.getLogger("bank_sync")

_MAX_GAP_DAYS = 7
_SANTANDER = "Santander Totta"
_REVOLUT = "Revolut Bank UAB"


def _account_ids(session: Session, institution: str) -> list[int]:
    return [a.id for a in session.exec(select(Account).where(Account.institution == institution)).all()]


def link_revolut_topups(session: Session) -> int:
    """Pair every unlinked Revolut top-up with its Santander debit. Returns pairs linked."""
    santander_ids = _account_ids(session, _SANTANDER)
    revolut_ids = _account_ids(session, _REVOLUT)
    if not santander_ids or not revolut_ids:
        return 0

    debits = [
        t for t in session.exec(select(Transaction).where(
            Transaction.account_id.in_(santander_ids),  # type: ignore[attr-defined]
            Transaction.transaction_type == TransactionType.DEBIT,
            Transaction.linked_transaction_id.is_(None),  # type: ignore[attr-defined]
            Transaction.paid_date.is_not(None),  # type: ignore[attr-defined]
        )).all()
        if "revolut" in t.provider.lower()
    ]
    credits = [
        t for t in session.exec(select(Transaction).where(
            Transaction.account_id.in_(revolut_ids),  # type: ignore[attr-defined]
            Transaction.transaction_type == TransactionType.CREDIT,
            Transaction.linked_transaction_id.is_(None),  # type: ignore[attr-defined]
            Transaction.paid_date.is_not(None),  # type: ignore[attr-defined]
        )).all()
        if t.provider.lower().startswith("top-up")
    ]

    unused: dict[int, list[Transaction]] = defaultdict(list)  # cents -> credits, oldest first
    for credit in sorted(credits, key=lambda t: t.paid_date):
        unused[round(credit.amount * 100)].append(credit)

    linked = 0
    for debit in sorted(debits, key=lambda t: t.paid_date):
        for credit in unused[round(debit.amount * 100)]:
            gap = (debit.paid_date - credit.paid_date).days
            if 0 <= gap <= _MAX_GAP_DAYS:
                unused[round(debit.amount * 100)].remove(credit)
                for row, other in ((debit, credit), (credit, debit)):
                    row.linked_transaction_id = other.id
                    row.transaction_type = TransactionType.TRANSFER
                    row.category = Category.TRANSFER
                    session.add(row)
                linked += 1
                break
    session.commit()
    if linked:
        logger.info("Linked %d Revolut top-up(s) to their Santander transfers", linked)
    return linked
