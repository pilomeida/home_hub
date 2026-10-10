"""Recover the direction of entries typed 'transfer' from their description.

A statement line typed 'transfer' lost its sign. Pedro's rule for the bank's own words: 'de' is FROM (money in),
'p/' or 'para' is TO (money out). Entries that are internal transfers (filed under a neutral node) are left alone,
and an unclear description stays a transfer for a human."""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session, select

from app.models.category_node import CategoryNode
from app.models.transaction import Transaction, TransactionType

_OUT = re.compile(r"(\bp/|\bpara\b|^transfer to\b|^to\b|intrabanc p/|\bsepa\+? p/)")
_IN = re.compile(r"(\btrf\.?imed\.? de\b|intrabanc de\b|\bsepa\+? de\b|^transfer from\b|^from\b|\btrf mbway de\b|\btransferencia de\b)")


def direction_from_description(description: Optional[str]) -> Optional[str]:
    """'out', 'in' or None when the words do not say."""
    n = unicodedata.normalize("NFKD", description or "").encode("ascii", "ignore").decode().lower().strip()
    out, inn = bool(_OUT.search(n)), bool(_IN.search(n))
    return "out" if out and not inn else ("in" if inn and not out else None)


@dataclass
class DirectionReport:
    to_debit: int = 0
    to_credit: int = 0
    unclear: int = 0
    moves: list[tuple[int, str, str]] = field(default_factory=list)  # (txn id, old type, new type)


def recover_transfer_directions(session: Session, dry_run: bool = True) -> DirectionReport:
    report = DirectionReport()
    nodes = {n.id: n for n in session.exec(select(CategoryNode)).all()}
    for t in session.exec(select(Transaction).where(Transaction.transaction_type == TransactionType.TRANSFER)).all():
        node = nodes.get(t.category_id) if t.category_id else None
        if node is not None and node.kind == "neutral":
            continue  # an internal transfer is a transfer
        if t.debt_id is not None or t.linked_transaction_id is not None:
            continue
        way = direction_from_description(t.provider)
        if way is None:
            report.unclear += 1
            continue
        new = TransactionType.DEBIT if way == "out" else TransactionType.CREDIT
        report.moves.append((t.id, t.transaction_type.value, new.value))
        report.to_debit += way == "out"
        report.to_credit += way == "in"
        if not dry_run:
            t.transaction_type = new
            session.add(t)
    if not dry_run:
        session.commit()
    return report
