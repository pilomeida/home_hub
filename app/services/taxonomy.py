"""Category tree: seeding, lookups, filing a transaction under a node."""

import re
from typing import Optional

from sqlmodel import Session, select

from app.models.category_node import CategoryNode
from app.models.transaction import Category, Transaction, TransactionType
from app.services.taxonomy_seed import LEGACY_TO_SLUG, SEED, SUB_LEGACY  # noqa: F401

UNSORTED_SLUG = "unsorted.needs-review.needs-review"
_KIND_LEGACY = {"out": Category.OTHER_EXPENSE, "in": Category.INCOME, "neutral": Category.TRANSFER}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("&", " ")).strip("-")


def ensure_taxonomy(session: Session) -> int:
    """Insert any seed node missing by slug. Idempotent. Returns nodes created."""
    existing = {n.slug for n in session.exec(select(CategoryNode)).all()}
    created = 0

    def upsert(slug, parent, level, name, kind, cadence, order):
        nonlocal created
        if slug in existing:
            node = session.exec(select(CategoryNode).where(CategoryNode.slug == slug)).one()
            if node.sort_order != order:  # keep the tree in the seed's order after the seed is reshuffled
                node.sort_order = order
                session.add(node)
            return node
        node = CategoryNode(slug=slug, parent_id=parent.id if parent else None, level=level,
                            name=name, kind=kind, cadence=cadence, sort_order=order)
        session.add(node)
        session.flush()
        existing.add(slug)
        created += 1
        return node

    order = 0
    for kind, groups in SEED.items():
        for gname, cats in groups:
            g = upsert(_slug(gname), None, 1, gname, kind, None, order := order + 1)
            for cname, cadence, _legacy, subs in cats:
                c = upsert(f"{g.slug}.{_slug(cname)}", g, 2, cname, kind, cadence, order := order + 1)
                for sub in subs:
                    sname, scadence = sub if isinstance(sub, tuple) else (sub, cadence)
                    upsert(f"{c.slug}.{_slug(sname)}", c, 3, sname, kind, scadence, order := order + 1)
    session.commit()
    return created


def get_node(session: Session, slug: str) -> CategoryNode:
    return session.exec(select(CategoryNode).where(CategoryNode.slug == slug)).one()


def leaf_slugs(session: Session) -> list[str]:
    return [n.slug for n in session.exec(
        select(CategoryNode).where(CategoryNode.level == 3).order_by(CategoryNode.sort_order)).all()]


def descendant_ids(session: Session, node_id: int) -> list[int]:
    ids, frontier = [node_id], [node_id]
    while frontier:
        children = session.exec(select(CategoryNode.id).where(CategoryNode.parent_id.in_(frontier))).all()
        ids += children
        frontier = list(children)
    return ids


def path_label(session: Session, node: CategoryNode) -> str:
    parts = [node.name]
    while node.parent_id:
        node = session.get(CategoryNode, node.parent_id)
        parts.append(node.name)
    return " › ".join(reversed(parts))


def file_transaction(session: Session, txn: Transaction, node: CategoryNode) -> None:
    """File txn under node and dual-write the legacy Category so old readers keep working."""
    txn.category_id = node.id
    if not node.slug.startswith("unsorted"):
        txn.category = legacy_category_for(session, node)
    session.add(txn)


def legacy_category_for(session: Session, node: CategoryNode) -> Category:
    if node.level == 3 and node.name in SUB_LEGACY:
        return SUB_LEGACY[node.name]
    cat = session.get(CategoryNode, node.parent_id) if node.level == 3 else node
    group = session.get(CategoryNode, cat.parent_id) if cat.level == 2 else None
    for kind, groups in SEED.items():
        if kind != node.kind:
            continue  # a category can exist under two kinds (Money lent out)
        for gname, cats in groups:
            if group is not None and gname != group.name:
                continue  # category names repeat across groups (Personal), so match the pair
            for cname, _cad, legacy, _subs in cats:
                if cname == cat.name and legacy is not None:
                    return legacy
    return _KIND_LEGACY[node.kind]


def flow_of(txn: Transaction, node: Optional[CategoryNode]) -> str:
    """'in' / 'out' / 'neutral' for colouring and totals. Unsorted follows the transaction's own type."""
    if node is None or node.slug.startswith("unsorted"):
        return "in" if txn.transaction_type == TransactionType.CREDIT else (
            "neutral" if txn.transaction_type == TransactionType.TRANSFER else "out")
    return node.kind


_legacy_for = legacy_category_for  # backwards-compatible alias


# A credit may sit under a spending node as a refund of what was bought there, except
# where money in is not a refund: loans, cash and savings (a person paying Pedro back).
REFUND_EXCLUDED_GROUPS = ("loans-debt", "cash-giving", "savings-investments")


def direction_matches(txn: Transaction, node: CategoryNode) -> bool:
    """Can txn be auto-filed under node? Unsorted and neutral nodes take anything;
    'in' nodes need a credit; 'out' nodes need a debit, or a credit that is a
    refund (see is_refund). A TRANSFER only ever fits neutral or Unsorted nodes."""
    if node.slug.startswith("unsorted") or node.kind == "neutral":
        return True
    t = txn.transaction_type
    if node.kind == "in":
        return t == TransactionType.CREDIT
    if t == TransactionType.CREDIT:
        return node.slug.split(".")[0] not in REFUND_EXCLUDED_GROUPS
    return t == TransactionType.DEBIT


def merchant_has_purchases(session: Session, merchant_id: Optional[int]) -> bool:
    if merchant_id is None:
        return False
    return session.exec(select(Transaction.id).where(
        Transaction.merchant_id == merchant_id,
        Transaction.transaction_type == TransactionType.DEBIT).limit(1)).first() is not None


def auto_fits(session: Session, txn: Transaction, node: CategoryNode) -> bool:
    """direction_matches for automatic filing: a credit goes under a spending node
    only as the refund of a merchant that was paid before (it has a debit). Otherwise
    it is money in from someone, e.g. a client paying Pedro, and must not reduce spending."""
    if not direction_matches(txn, node):
        return False
    if is_refund(txn, node):
        # A person's transfer to you is not a shop refund, even when you also paid that person.
        return merchant_has_purchases(session, txn.merchant_id) and not looks_like_person_transfer(txn.provider)
    return True


def default_node_id(merchant, txn) -> Optional[int]:
    """The merchant's default for THIS entry's direction: a credit takes the credit default when there is one."""
    if txn.transaction_type == TransactionType.CREDIT and getattr(merchant, "default_credit_category_id", None):
        return merchant.default_credit_category_id
    return merchant.default_category_id


_TRANSFER_TEXT = re.compile(r"(\btrf\b|transfer|\bmbway\b|\bmb way\b|\bimed\b|intrabanc|\bsepa\b)", re.IGNORECASE)


def looks_like_person_transfer(description: Optional[str]) -> bool:
    """A bank transfer text, not a shop's refund (CCR-/CPR-/estorno/reembolso)."""
    return bool(_TRANSFER_TEXT.search(description or ""))


def is_refund(txn: Transaction, node: Optional[CategoryNode]) -> bool:
    """A refund is not a category: it is a credit filed under the spending node it
    refunds. Budgets already net it off (a credit under an 'out' node is negative spend)."""
    return (node is not None and node.kind == "out" and not node.slug.startswith("unsorted")
            and txn.transaction_type == TransactionType.CREDIT)
