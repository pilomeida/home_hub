"""Provider-level classification: normalize provider texts, group them, apply a category to a group,
and remember it as a rule for future entries."""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.models.category_node import CategoryNode
from app.models.merchant import Merchant
from app.models.provider_rule import ProviderRule
from app.models.transaction import Transaction, TransactionType
from app.services.taxonomy import auto_fits, file_transaction


def provider_key(text: Optional[str]) -> str:
    """lowercase, accents removed, every run of digits -> '#', whitespace collapsed.
    'TRF MBWAY P/XXXXX1225' -> 'trf mbway p/xxxxx#'; 'COMPRA 3315 SODIMAFRA' -> 'compra # sodimafra'."""
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\d+", "#", s)
    return re.sub(r"\s+", " ", s).strip(" -/*.")


def find_rule_node(session: Session, provider: Optional[str]) -> Optional[CategoryNode]:
    key = provider_key(provider)
    if not key:
        return None
    rule = session.exec(select(ProviderRule).where(ProviderRule.key == key)).first()
    return session.get(CategoryNode, rule.node_id) if rule else None


def provider_fits(session: Session, t: Transaction, node: CategoryNode) -> bool:
    """A human picked this category for these exact entries: a TRANSFER-type row may take any node
    (budgets count it as spend/income there); credits and debits still obey direction and refund rules."""
    if t.transaction_type == TransactionType.TRANSFER:
        return True
    return auto_fits(session, t, node)


@dataclass
class ProviderGroup:
    key: str
    example: str
    merchants: list[str]
    entries: int
    total: float
    category_label: str        # the one category all entries share, or "N categories"
    unsorted: int
    rule: bool                 # a rule already exists for this key


@dataclass
class GroupPage:
    rows: list[ProviderGroup] = field(default_factory=list)
    total_count: int = 0
    page: int = 1
    pages: int = 1


PAGE_SIZE = 50


def _label(nodes: dict, node_id: Optional[int]) -> str:
    node = nodes.get(node_id)
    if node is None or node.slug.startswith("unsorted"):
        return "Needs review"
    parts = [node.name]
    while node.parent_id:
        node = nodes[node.parent_id]
        parts.append(node.name)
    return " › ".join(reversed(parts))


def filtered_transactions(session: Session, q: str = "", node_slug: str = "", date_from: Optional[date] = None,
                          date_to: Optional[date] = None) -> list[Transaction]:
    from app.services.taxonomy import descendant_ids
    stmt = select(Transaction)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(Transaction.provider.ilike(like)
                          | Transaction.merchant_id.in_(select(Merchant.id).where(Merchant.canonical_name.ilike(like))))
    if date_from:
        stmt = stmt.where(Transaction.paid_date >= date_from)
    if date_to:
        stmt = stmt.where(Transaction.paid_date <= date_to)
    if node_slug == "unsorted":
        un = select(CategoryNode.id).where(CategoryNode.slug.like("unsorted%"))
        stmt = stmt.where(Transaction.category_id.is_(None) | Transaction.category_id.in_(un))
    elif node_slug:
        node = session.exec(select(CategoryNode).where(CategoryNode.slug == node_slug)).first()
        stmt = stmt.where(Transaction.category_id.in_(descendant_ids(session, node.id) if node else [-1]))
    return list(session.exec(stmt).all())


def group_transactions(rows: list[Transaction]) -> dict[str, list[Transaction]]:
    groups: dict[str, list[Transaction]] = {}
    for t in rows:
        groups.setdefault(provider_key(t.provider), []).append(t)
    return groups


def list_provider_groups(session: Session, q: str = "", node_slug: str = "", date_from: Optional[date] = None,
                         date_to: Optional[date] = None, min_entries: int = 1, sort: str = "entries",
                         page: int = 1) -> GroupPage:
    nodes = {n.id: n for n in session.exec(select(CategoryNode)).all()}
    names = {m.id: m.canonical_name for m in session.exec(select(Merchant)).all()}
    rules = {r.key for r in session.exec(select(ProviderRule)).all()}
    out = []
    for key, txns in group_transactions(filtered_transactions(session, q, node_slug, date_from, date_to)).items():
        if len(txns) < min_entries:
            continue
        cats = {t.category_id for t in txns}
        merchants = sorted({names.get(t.merchant_id, "—") for t in txns})
        label = _label(nodes, next(iter(cats))) if len(cats) == 1 else f"{len(cats)} categories"
        un = sum(1 for t in txns if t.category_id is None or nodes.get(t.category_id) is None
                 or nodes[t.category_id].slug.startswith("unsorted"))
        out.append(ProviderGroup(key, txns[0].provider, merchants[:3], len(txns), round(sum(t.amount for t in txns), 2),
                                 label, un, key in rules))
    order = {"entries": lambda g: (-g.entries, g.key), "amount": lambda g: (-g.total, g.key),
             "name": lambda g: g.key}.get(sort, lambda g: (-g.entries, g.key))
    out.sort(key=order)
    pages = max(1, -(-len(out) // PAGE_SIZE))
    page = min(max(1, page), pages)
    return GroupPage(out[(page - 1) * PAGE_SIZE: page * PAGE_SIZE], len(out), page, pages)


@dataclass
class ProviderApplyReport:
    groups: int = 0
    filed_from_unsorted: int = 0
    moved_from_elsewhere: int = 0
    left_alone: int = 0
    rules_saved: int = 0


def apply_to_providers(session: Session, keys: list[str], node: CategoryNode, *, q: str = "", node_slug: str = "",
                       date_from: Optional[date] = None, date_to: Optional[date] = None,
                       refile_existing: bool = True, remember: bool = True, per_entry_merchants: bool = False,
                       dry_run: bool = False) -> ProviderApplyReport:
    """File every entry whose provider key is in `keys` (within the same search/date/category filter
    the page shows) under `node`; optionally save a rule per key for future entries. Loan-linked entries
    never move. `per_entry_merchants` marks the entries' merchants as channels (by_provider)."""
    if node.level != 3 or node.slug.startswith("unsorted"):
        raise ValueError("pick a real sub-category")
    report = ProviderApplyReport()
    groups = group_transactions(filtered_transactions(session, q, node_slug, date_from, date_to))
    for key in keys:
        txns = groups.get(key)
        if not txns:
            continue
        report.groups += 1
        if per_entry_merchants and not dry_run:
            for mid in {t.merchant_id for t in txns if t.merchant_id}:
                merchant = session.get(Merchant, mid)
                if merchant is not None and not merchant.by_provider:
                    merchant.by_provider = True
                    session.add(merchant)
        for t in txns:
            if t.debt_id is not None or t.category_id == node.id:
                continue
            current = session.get(CategoryNode, t.category_id) if t.category_id else None
            unsorted = current is None or current.slug.startswith("unsorted")
            if not unsorted and not refile_existing:
                continue
            if not provider_fits(session, t, node):
                report.left_alone += 1
                continue
            report.filed_from_unsorted += int(unsorted)
            report.moved_from_elsewhere += int(not unsorted)
            if not dry_run:
                file_transaction(session, t, node)
        if remember:
            report.rules_saved += 1
            if not dry_run:
                rule = session.exec(select(ProviderRule).where(ProviderRule.key == key)).first()
                if rule is None:
                    session.add(ProviderRule(key=key, example=txns[0].provider, node_id=node.id))
                else:
                    rule.node_id = node.id
                    session.add(rule)
    if not dry_run:
        session.commit()
    return report
