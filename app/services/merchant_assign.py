"""Give merchants a category in bulk, and list merchants for the bulk page."""

from dataclasses import dataclass, field

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.category_node import CategoryNode
from app.models.merchant import Merchant
from app.models.transaction import Transaction, TransactionType
from app.services.taxonomy import auto_fits, file_transaction, legacy_category_for


@dataclass
class AssignReport:
    merchants: int = 0
    filed_from_unsorted: int = 0
    moved_from_elsewhere: int = 0
    left_alone: int = 0  # entries that cannot take the node (a credit with no purchase to refund, ...)


def assign_category(session: Session, merchant_ids: list[int], node: CategoryNode, *,
                    refile_existing: bool = True, confirm: bool = True,
                    dry_run: bool = False) -> AssignReport:
    """Make `node` the merchants' category. The default always changes (so future entries follow).
    Unsorted entries are filed; with refile_existing, entries filed elsewhere move too. Loan-linked
    entries and TRANSFERs never move; a credit moves only where auto_fits allows (refund rule)."""
    if node.level != 3 or node.slug.startswith("unsorted"):
        raise ValueError("pick a real sub-category")
    report = AssignReport()
    for merchant in session.exec(select(Merchant).where(Merchant.id.in_(merchant_ids))).all():
        report.merchants += 1
        if not dry_run:
            merchant.default_category_id = node.id
            merchant.default_category = legacy_category_for(session, node)
            if confirm:
                merchant.confirmed = True
            session.add(merchant)
        for t in session.exec(select(Transaction).where(Transaction.merchant_id == merchant.id)).all():
            if t.debt_id is not None or t.transaction_type == TransactionType.TRANSFER or t.category_id == node.id:
                continue
            current = session.get(CategoryNode, t.category_id) if t.category_id else None
            unsorted = current is None or current.slug.startswith("unsorted")
            if not unsorted and not refile_existing:
                continue
            if not auto_fits(session, t, node):
                report.left_alone += 1
                continue
            if unsorted:
                report.filed_from_unsorted += 1
            else:
                report.moved_from_elsewhere += 1
            if not dry_run:
                file_transaction(session, t, node)
    if not dry_run:
        session.commit()
    return report


PAGE_SIZE = 50
SORTS = {"entries": "entries", "amount": "total", "name": "name"}


@dataclass
class MerchantRow:
    id: int
    name: str
    category_label: str
    confirmed: bool
    entries: int
    total: float
    elsewhere: int  # entries filed somewhere other than the merchant's category


@dataclass
class MerchantPage:
    rows: list[MerchantRow] = field(default_factory=list)
    total_count: int = 0
    page: int = 1
    pages: int = 1


def list_merchants(session: Session, q: str = "", scope: str = "all", sort: str = "entries",
                   page: int = 1, node_slug: str = "", date_from=None, date_to=None,
                   min_entries: int = 1) -> MerchantPage:
    """Merchants with at least one entry. scope: all | undecided (no category yet or not confirmed).
    q matches the merchant name or any provider text; date_from/date_to restrict the entries counted;
    node_slug filters on the merchant's category ("unsorted" = none yet); min_entries on the count."""
    from app.services.taxonomy import descendant_ids
    nodes = {n.id: n for n in session.exec(select(CategoryNode)).all()}
    stmt = (select(Merchant.id, Merchant.canonical_name, Merchant.default_category_id, Merchant.confirmed,
                   func.count(Transaction.id), func.coalesce(func.sum(Transaction.amount), 0.0))
            .join(Transaction, Transaction.merchant_id == Merchant.id).group_by(Merchant.id))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(Merchant.canonical_name.ilike(like) | Transaction.provider.ilike(like))
    if date_from:
        stmt = stmt.where(Transaction.paid_date >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.paid_date <= date_to)
    if node_slug == "unsorted":
        stmt = stmt.where(Merchant.default_category_id.is_(None))
    elif node_slug:
        node = next((n for n in nodes.values() if n.slug == node_slug), None)
        stmt = stmt.where(Merchant.default_category_id.in_(descendant_ids(session, node.id) if node else [-1]))
    if min_entries > 1:
        stmt = stmt.having(func.count(Transaction.id) >= min_entries)
    if scope == "undecided":
        stmt = stmt.where(Merchant.default_category_id.is_(None) | (Merchant.confirmed == False))  # noqa: E712
    key = SORTS.get(sort, "entries")
    order = {"entries": func.count(Transaction.id).desc(), "total": func.sum(Transaction.amount).desc(),
             "name": Merchant.canonical_name}[key]
    all_rows = session.exec(stmt.order_by(order, Merchant.id)).all()
    pages = max(1, -(-len(all_rows) // PAGE_SIZE))
    page = min(max(1, page), pages)
    chunk = all_rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    ids = [r[0] for r in chunk]
    elsewhere = {}
    if ids:
        for mid, default_id, n in session.exec(
                select(Merchant.id, Merchant.default_category_id, func.count(Transaction.id))
                .join(Transaction, Transaction.merchant_id == Merchant.id)
                .where(Merchant.id.in_(ids), Transaction.debt_id.is_(None),
                       Transaction.transaction_type != TransactionType.TRANSFER,
                       (Transaction.category_id.is_(None)) | (Transaction.category_id != Merchant.default_category_id))
                .group_by(Merchant.id)).all():
            elsewhere[mid] = n
    rows = []
    for mid, name, default_id, confirmed, n, total in chunk:
        node = nodes.get(default_id)
        label = " › ".join(_path(nodes, node)) if node else ""
        rows.append(MerchantRow(mid, name, label, bool(confirmed), n, float(total), elsewhere.get(mid, 0)))
    return MerchantPage(rows, len(all_rows), page, pages)


def _path(nodes: dict, node: CategoryNode) -> list[str]:
    parts = [node.name]
    while node.parent_id:
        node = nodes[node.parent_id]
        parts.append(node.name)
    return list(reversed(parts))
