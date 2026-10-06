"""One-off (re-runnable) backfill: file existing transactions into the category tree."""

from dataclasses import dataclass

from sqlmodel import Session, select

from app.models.category_node import CategoryNode
from app.models.merchant import Merchant
from app.models.transaction import Transaction, TransactionType
from app.services.taxonomy import UNSORTED_SLUG, direction_matches, file_transaction, get_node
from app.services.taxonomy_seed import LEGACY_TO_SLUG


@dataclass
class RefileReport:
    filed_from_merchant: int = 0
    filed_from_legacy: int = 0
    sent_to_review: int = 0
    already_filed: int = 0


def refile_all(session: Session, dry_run: bool = True) -> RefileReport:
    report = RefileReport()
    unsorted = get_node(session, UNSORTED_SLUG)
    # Pre-pass: a confirmed merchant with no node gets one from the MERCHANT's own
    # legacy value only (never from a transaction), so row order cannot matter.
    derived: dict[int, int] = {}
    for m in session.exec(select(Merchant).where(
            Merchant.confirmed == True, Merchant.default_category_id.is_(None))).all():  # noqa: E712
        slug = LEGACY_TO_SLUG.get(m.default_category)
        if slug:
            derived[m.id] = get_node(session, slug).id
            if not dry_run:
                m.default_category_id = derived[m.id]
                session.add(m)
    for t in session.exec(select(Transaction)).all():
        if t.category_id is not None:
            report.already_filed += 1
            continue
        merchant = session.get(Merchant, t.merchant_id) if t.merchant_id else None
        merchant_node_id = (merchant.default_category_id or derived.get(merchant.id)) if (
            merchant and merchant.confirmed) else None
        if merchant_node_id:
            node, bucket = session.get(CategoryNode, merchant_node_id), "filed_from_merchant"
        elif LEGACY_TO_SLUG.get(t.category):
            node, bucket = get_node(session, LEGACY_TO_SLUG[t.category]), "filed_from_legacy"
        else:
            node, bucket = unsorted, "sent_to_review"
        if not direction_matches(t, node):
            node, bucket = unsorted, "sent_to_review"
        setattr(report, bucket, getattr(report, bucket) + 1)
        if not dry_run:
            file_transaction(session, t, node)
    if not dry_run:
        session.commit()
    return report


@dataclass
class ReclassifyReport:
    merchants_asked: int = 0
    merchants_resolved: int = 0
    transactions_filed: int = 0
    still_unsorted: int = 0
    failed: int = 0


async def reclassify_unsorted(session: Session, gateway=None) -> ReclassifyReport:
    """Ask the LLM once per distinct merchant that has Unsorted transactions and
    file them under the answer. The merchant stays confirmed=False so Pedro can
    still correct it on the Needs Review page. Commits per merchant."""
    from app.services.classification_engine import MerchantResolutionError, resolve_merchant_via_llm

    report = ReclassifyReport()
    unsorted = get_node(session, UNSORTED_SLUG)
    txns = session.exec(select(Transaction).where(
        Transaction.category_id == unsorted.id, Transaction.merchant_id.is_not(None))).all()
    by_merchant: dict[int, list[Transaction]] = {}
    for t in txns:
        by_merchant.setdefault(t.merchant_id, []).append(t)

    for merchant_id, group in by_merchant.items():
        merchant = session.get(Merchant, merchant_id)
        if merchant is None:
            continue
        if merchant.confirmed and merchant.default_category_id is not None:
            continue  # Pedro's own decisions are never touched; rows stay Unsorted
        legacy_slug = LEGACY_TO_SLUG.get(merchant.default_category) if merchant.confirmed else None
        if legacy_slug:
            # Confirmed but never given a node: its legacy category is his decision.
            node = get_node(session, legacy_slug)
            merchant.default_category_id = node.id
            session.add(merchant)
            fitting = [t for t in group if direction_matches(t, node)]
            for t in fitting:
                file_transaction(session, t, node)
            report.merchants_resolved += 1
            report.transactions_filed += len(fitting)
            report.still_unsorted += len(group) - len(fitting)
            session.commit()
            continue
        report.merchants_asked += 1
        is_credit = any(t.transaction_type == TransactionType.CREDIT for t in group)
        try:
            resolved = await resolve_merchant_via_llm(
                group[0].provider, session, gateway=gateway, is_credit=is_credit)
        except MerchantResolutionError:
            report.failed += 1
            continue
        if resolved.node_slug == UNSORTED_SLUG:
            report.still_unsorted += len(group)
            continue
        node = get_node(session, resolved.node_slug)
        merchant.default_category_id = node.id
        merchant.default_category = resolved.category
        session.add(merchant)
        fitting = [t for t in group if direction_matches(t, node)]
        for t in fitting:
            file_transaction(session, t, node)
        report.merchants_resolved += 1
        report.transactions_filed += len(fitting)
        report.still_unsorted += len(group) - len(fitting)
        session.commit()
    return report
