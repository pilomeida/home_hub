"""How "Waiting for you" is shown (PLAN step 10e): the grouped list, one item at a time with its paper, and the
merchants as a table. Presentation only: the items, their answers and what an answer does stay in
`app/services/review.py` (existing behaviour, new look)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlmodel import Session, func, select

from app.models.document import Document
from app.models.review import ReviewItem
from app.models.transaction import Transaction
from app.services import review

GROUPS = [("inbox", "Inbox", "papers"), ("money", "Money", "to check"), ("health", "Health", "to confirm")]


@dataclass
class Group:
    key: str
    label: str
    count: int
    unit: str
    first_title: str
    oldest_days: Optional[int]
    link: str
    button: str


def groups(session: Session, now: Optional[datetime] = None) -> list[Group]:
    """One row per area (the menu's count), then the merchants on their own line."""
    now = now or datetime.utcnow()
    out = []
    for key, label, unit in GROUPS + [(review.MERCHANTS, "Merchants", "unconfirmed")]:
        merchants = key == review.MERCHANTS
        items = review.waiting(session, None if merchants else key, now, merchants=merchants)
        if not items:
            continue
        oldest = min(i.created_at for i in items)
        out.append(Group(key, label, len(items), unit, items[0].title, (now - oldest).days,
                         f"/waiting?area={key}" if merchants else f"/waiting/one?area={key}",
                         "Open the table" if merchants else "Open the first"))
    return out


def document_of(session: Session, item: ReviewItem) -> Optional[Document]:
    """The paper an item is about, when it is about one: a statement that doesn't add up, a line on a statement, a
    health paper read with doubts."""
    kind, _, ident = (item.ref or "").partition(":")
    if not ident.isdigit():
        return None
    if item.kind in ("statement_mismatch", "health_doubts"):
        return session.get(Document, int(ident))
    if item.kind == "to_check_line":
        txn = session.get(Transaction, int(ident))
        return session.get(Document, txn.document_id) if txn is not None else None
    return None


@dataclass
class One:
    card: review.Card
    document: Optional[Document]
    position: int                  # 0-based among what is waiting in this area
    total: int


def one(session: Session, area: Optional[str], position: int = 0) -> Optional[One]:
    """The item at `position` among what is waiting in `area` (all areas, without the merchants, when None)."""
    merchants = area == review.MERCHANTS
    key = None if merchants else area
    total = (review.merchant_count(session) if merchants else
             review.area_counts(session, merchants=False).get(area, 0) if area else review.count(session, merchants=False))
    if total == 0:
        return None
    position = max(0, min(position, total - 1))
    found = review.waiting(session, key, offset=position, limit=1, merchants=merchants)
    if not found:
        return None
    return One(review.card(found[0]), document_of(session, found[0]), position, total)


@dataclass
class MerchantRow:
    item: ReviewItem
    name: str
    guess: Optional[str]           # the category the Hub filed it under, when it is a real one
    lines: int
    can_confirm: bool


def merchant_rows(session: Session, items: list[ReviewItem]) -> list[MerchantRow]:
    from app.models.category_node import CategoryNode
    from app.models.merchant import Merchant
    from app.services.taxonomy import path_label
    ids = [int(i.ref.split(":")[1]) for i in items if i.kind == review.MERCHANT]
    counts = dict(session.exec(select(Transaction.merchant_id, func.count()).where(
        Transaction.merchant_id.in_(ids)).group_by(Transaction.merchant_id)).all()) if ids else {}
    out = []
    for i in items:
        if i.kind != review.MERCHANT:
            continue
        m = session.get(Merchant, int(i.ref.split(":")[1]))
        if m is None:
            continue
        node = session.get(CategoryNode, m.default_category_id) if m.default_category_id else None
        known = node is not None and not node.slug.startswith("unsorted") and node.level == 3
        out.append(MerchantRow(i, m.canonical_name, path_label(session, node) if known else None,
                               counts.get(m.id, 0), any(o["value"] == review.APPROVE for o in review.options(i))))
    return out


__all__ = ["Group", "groups", "document_of", "One", "one", "MerchantRow", "merchant_rows"]
