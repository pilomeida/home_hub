"""One category per merchant: bring every transaction of a merchant under a single node.

Target node, in order: a keyword rule (merchant_rules); else a confirmed merchant's own
default; else the node most of its debits already sit in (ties: larger total, then the
merchant's default); else its default. Credits follow it as refunds where direction_matches
allows (spending nodes outside loans/cash/savings); otherwise they are left where they are.
Loan-linked rows and TRANSFERs are never moved. Every move is logged for reversal."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.models.category_node import CategoryNode
from app.models.merchant import Merchant
from app.models.transaction import Transaction, TransactionType
from app.services.merchant_rules import keyword_node_slug
from app.services.taxonomy import auto_fits, file_transaction, get_node, legacy_category_for

_UNSORTED = "unsorted"


@dataclass
class ReconcileReport:
    merchants_changed: int = 0
    merchants_unresolved: int = 0
    transactions_moved: int = 0
    credits_left: int = 0  # credits that cannot follow the merchant's spending node
    moves: list[tuple[int, int | None, int]] = field(default_factory=list)  # (txn, old node, new node)
    merchant_changes: list[tuple[int, int | None, int]] = field(default_factory=list)


def _is_unsorted(node) -> bool:
    return node is None or node.slug.startswith(_UNSORTED)


def _pick_target(session: Session, merchant: Merchant, txns: list[Transaction],
                 nodes: dict[int, CategoryNode]):
    slug = keyword_node_slug(merchant.canonical_name, *(t.provider for t in txns))
    if slug:
        return get_node(session, slug)
    default = nodes.get(merchant.default_category_id) if merchant.default_category_id else None
    if merchant.confirmed and not _is_unsorted(default):
        return default
    count, total = Counter(), defaultdict(float)
    for t in txns:
        node = nodes.get(t.category_id) if t.category_id else None
        if t.transaction_type == TransactionType.DEBIT and not _is_unsorted(node):
            count[node.id] += 1
            total[node.id] += t.amount
    if count:
        best = max(count, key=lambda n: (count[n], total[n], n == merchant.default_category_id))
        return nodes[best]
    return None if _is_unsorted(default) else default


def reconcile_merchants(session: Session, dry_run: bool = True, only_ids: list[int] | None = None) -> ReconcileReport:
    report = ReconcileReport()
    nodes = {n.id: n for n in session.exec(select(CategoryNode)).all()}
    by_merchant: dict[int, list[Transaction]] = defaultdict(list)
    for t in session.exec(select(Transaction).where(Transaction.merchant_id.is_not(None))).all():
        by_merchant[t.merchant_id].append(t)
    for merchant in session.exec(select(Merchant)).all():
        if only_ids is not None and merchant.id not in only_ids:
            continue
        if merchant.by_provider or merchant.merged_into_id is not None:
            continue  # a channel (MB Way Transfer ...): its entries are classified one by one
        txns = by_merchant.get(merchant.id, [])
        movable = [t for t in txns if t.debt_id is None and t.transaction_type != TransactionType.TRANSFER]
        if not movable:
            continue
        target = _pick_target(session, merchant, movable, nodes)
        # Money in may have its own default (a client's payment is income, a benefit is not a tax).
        credit_target = nodes.get(merchant.default_credit_category_id) if merchant.default_credit_category_id else None
        if credit_target is None and target is not None and target.kind == "in":
            credit_target = target
        if target is None and credit_target is None:
            report.merchants_unresolved += 1
            continue
        if target is not None and target.kind == "in":
            target = None  # an inflow node is the credit default, not the outflow one
        if target is not None and merchant.default_category_id != target.id:
            report.merchant_changes.append((merchant.id, merchant.default_category_id, target.id))
            report.merchants_changed += 1
            if not dry_run:
                merchant.default_category_id = target.id
                merchant.default_category = legacy_category_for(session, target)
                session.add(merchant)
        for t in movable:
            aim = credit_target if (t.transaction_type == TransactionType.CREDIT and credit_target is not None) else target
            if aim is None:
                continue
            if t.category_id == aim.id:
                continue
            if not auto_fits(session, t, aim):
                if t.transaction_type == TransactionType.CREDIT:
                    report.credits_left += 1
                continue
            report.moves.append((t.id, t.category_id, aim.id))
            report.transactions_moved += 1
            if not dry_run:
                file_transaction(session, t, aim)
    if not dry_run:
        session.commit()
    return report
