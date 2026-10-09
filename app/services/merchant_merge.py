"""Merge merchants: one counterparty, one merchant.

The sources keep their rows as aliases (`merged_into_id`), so the bank text that created them still
resolves to the survivor when it arrives again; their transactions move to the survivor. Nothing is
deleted and a merge can be undone from the log (transaction ids and the sources' ids)."""

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Optional

from sqlalchemy import text
from sqlmodel import Session, select

from app.models.merchant import Merchant

MAX_CHAIN = 10


def resolve_merchant(session: Session, merchant: Optional[Merchant]) -> Optional[Merchant]:
    """The survivor of a merchant that was merged (itself when it was not)."""
    seen = 0
    while merchant is not None and merchant.merged_into_id is not None and seen < MAX_CHAIN:
        merchant = session.get(Merchant, merchant.merged_into_id)
        seen += 1
    return merchant


def name_key(name: Optional[str]) -> str:
    """'Santander', 'SANTANDER ' and 'Santandér' are one name."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.split(r"[^a-z0-9]+", s)).strip()


def find_merchant_by_name(session: Session, name: str) -> Optional[Merchant]:
    """The existing merchant of that name (a merged one answers as its survivor)."""
    wanted = name_key(name)
    if not wanted:
        return None
    for merchant in session.exec(select(Merchant).where(Merchant.merged_into_id.is_(None))).all():
        if name_key(merchant.canonical_name) == wanted:
            return merchant
    return None


@dataclass
class MergeReport:
    merchants_merged: int = 0
    transactions_moved: int = 0
    moved_ids: list[int] = field(default_factory=list)


def merge_merchants(session: Session, source_ids: list[int], target_id: int, *,
                    new_name: Optional[str] = None, dry_run: bool = False) -> MergeReport:
    target = resolve_merchant(session, session.get(Merchant, target_id))
    if target is None:
        raise ValueError(f"merchant {target_id} not found")
    sources = [m for m in session.exec(select(Merchant).where(Merchant.id.in_(source_ids))).all() if m.id != target.id]
    report = MergeReport()
    ids = [m.id for m in sources]
    if ids:
        marks = ",".join(str(i) for i in ids)
        report.moved_ids = [r[0] for r in session.execute(text(f"SELECT id FROM transactions WHERE merchant_id IN ({marks})")).all()]
    report.merchants_merged = len(sources)
    report.transactions_moved = len(report.moved_ids)
    if dry_run:
        return report
    for source in sources:
        if source.merged_into_id == target.id:
            continue
        source.merged_into_id = target.id
        session.add(source)
        # earlier merges into this source now point straight at the survivor
        for earlier in session.exec(select(Merchant).where(Merchant.merged_into_id == source.id)).all():
            earlier.merged_into_id = target.id
            session.add(earlier)
    if ids:
        session.execute(text(f"UPDATE transactions SET merchant_id = :t WHERE merchant_id IN ({marks})"), {"t": target.id})
    if new_name:
        target.canonical_name = new_name
        session.add(target)
    session.commit()
    return report
