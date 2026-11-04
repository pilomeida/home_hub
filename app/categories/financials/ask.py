"""Financials' contribution to Ask: spend aggregates and transaction search.
Spending is counted as the budget and the Overview count it (budget_service.spend_of), on the spending tree."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Optional
from urllib.parse import urlencode

from sqlmodel import Session, select

from app.models.category_node import CategoryNode
from app.models.document import Document
from app.models.merchant import Merchant
from app.models.transaction import Transaction
from app.privacy.reach import reach_for
from app.privacy.tiers import Tier
from app.services.ask.contracts import AskTool, Citable, ToolOutput
from app.services.budget_service import spend_of
from app.services.taxonomy import _slug
from app.services.taxonomy_seed import SEED

_TOP_N = 25
# What the model may ask about: the spending tree's groups and their categories ("food", "food.groceries").
_SPENDING_SLUGS = [s for g, cats in SEED["out"] for s in
                   [_slug(g)] + [f"{_slug(g)}.{_slug(c)}" for c, _cad, _subs in cats]]


def _txn_url(**params) -> str:
    return "/financials/transactions?" + urlencode({k: v for k, v in params.items() if v not in (None, "")})


def _readable(session: Session, transactions: list) -> list:
    """Only transactions read from a document whose type the AI may read (app/privacy/reach.py);
    all of them while levels are not binding."""
    allowed = reach_for(session).included_document_ids(session)
    return list(transactions) if allowed is None else [t for t in transactions if t.document_id in allowed]


def _private(session: Session, transactions: list) -> tuple[Optional[Tier], str]:
    """HIGH (and plain words for the notice) when any of these transactions was read from a document of a
    private type: the run must then go on at High."""
    reach = reach_for(session)
    docs = {t.document_id: session.get(Document, t.document_id) for t in transactions}
    n = sum(1 for t in transactions if reach.tier_of_source(docs[t.document_id]) == Tier.HIGH)
    return (Tier.HIGH, f"{n} private transaction{'s' if n != 1 else ''}") if n else (None, "")


def _merchant_names(session: Session) -> dict[int, str]:
    return {m.id: m.canonical_name for m in session.exec(select(Merchant)).all()}


def _label(nodes: dict[int, CategoryNode], node: Optional[CategoryNode]) -> str:
    """"Group › Category" on the spending tree; "Unsorted" for a line not filed yet."""
    if node is None:
        return "Unsorted"
    parts = [node]
    while parts[-1].parent_id is not None:
        parts.append(nodes[parts[-1].parent_id])
    return " › ".join(n.name for n in reversed(parts[-2:]))


def _spending(session: Session, args: dict) -> ToolOutput:
    date_from, date_to = date.fromisoformat(args["date_from"]), date.fromisoformat(args["date_to"])
    group_by, category = args.get("group_by", "category"), args.get("category")
    nodes = {n.id: n for n in session.exec(select(CategoryNode)).all()}
    within: Optional[set[int]] = None
    if category:
        top = next((n for n in nodes.values() if n.slug == category), None)
        if top is None:
            return ToolOutput(text=f"There is no spending category {category!r}. The categories are: "
                                   + ", ".join(_SPENDING_SLUGS) + ".", is_error=True)
        within = {n.id for n in nodes.values() if n.slug == top.slug or n.slug.startswith(top.slug + ".")}
    statement = select(Transaction).where(Transaction.counts(),
                                          Transaction.paid_date >= date_from, Transaction.paid_date <= date_to)
    merchants = _merchant_names(session) if group_by == "merchant" else {}
    totals: dict[str, list] = defaultdict(lambda: [0.0, 0])
    counted = []
    for t in _readable(session, session.exec(statement).all()):
        node = nodes.get(t.category_id) if t.category_id is not None else None
        amount = spend_of(t, node)
        if amount is None or (within is not None and (node is None or node.id not in within)):
            continue
        counted.append(t)
        key = (merchants.get(t.merchant_id, t.description) if group_by == "merchant"
               else f"{t.paid_date:%Y-%m}" if group_by == "month" else _label(nodes, node))
        totals[key][0] += amount
        totals[key][1] += 1
    ordered = sorted(totals.items()) if group_by == "month" else sorted(totals.items(), key=lambda kv: -kv[1][0])
    lines = [f"Spending (by paid date, refunds taken off, as the budget counts it) {date_from}–{date_to}, grouped by {group_by}"
             + (f", category {category}" if category else "") + f": total €{sum(v[0] for v in totals.values()):,.2f}"]
    lines += [f"- {k}: €{v[0]:,.2f} ({v[1]} payments)" for k, v in ordered[:_TOP_N]]
    citable = Citable(
        ref=f"financials:spend:{category or 'all'}:{date_from}:{date_to}:{group_by}",
        label=f"Transactions {date_from} to {date_to}" + (f" ({category})" if category else ""),
        url=_txn_url(date_from=date_from.isoformat(), date_to=date_to.isoformat(), category_node=category))
    tier, opened = _private(session, counted)
    return ToolOutput(text="\n".join(lines), citables=[citable], used_raw_sources=True, tier=tier, opened=opened)


def _find_transactions(session: Session, args: dict) -> ToolOutput:
    needle = (args.get("query") or "").lower()
    statement = select(Transaction).where(Transaction.counts())
    if args.get("date_from"):
        statement = statement.where(Transaction.paid_date >= date.fromisoformat(args["date_from"]))
    if args.get("date_to"):
        statement = statement.where(Transaction.paid_date <= date.fromisoformat(args["date_to"]))
    merchants = _merchant_names(session)
    nodes = {n.id: n for n in session.exec(select(CategoryNode)).all()}
    matches = [t for t in _readable(session, session.exec(statement.order_by(Transaction.paid_date.desc())).all())
               if needle in t.description.lower() or needle in merchants.get(t.merchant_id, "").lower()]
    shown = matches[:40]
    lines = [f"{len(matches)} matching transactions (showing {len(shown)}):"] + [
        f"financials:txn:{t.id} | {t.paid_date or '-'} | {merchants.get(t.merchant_id, t.description)} | "
        f"{t.transaction_type.value} €{t.amount:,.2f} | {_label(nodes, nodes.get(t.category_id))}" for t in shown]
    citables = [Citable(ref=f"financials:txn:{t.id}", label=f"Transaction: {t.description} ({t.paid_date})",
                        url=_txn_url(transaction_id=t.id)) for t in shown]
    tier, opened = _private(session, matches)       # the total and the count cover every match, not only those shown
    return ToolOutput(text="\n".join(lines), citables=citables, used_raw_sources=True, tier=tier, opened=opened)


FINANCIALS_ASK_TOOLS: tuple[AskTool, ...] = (
    AskTool(name="financials_spending",
            description="Total household spending (by paid date, refunds taken off, counted as the budget counts "
                        "it) over a date range, grouped by category, merchant or month. A category is a group of the "
                        "spending tree ('food') or one of its categories ('food.groceries'). "
                        "Use for 'how much did we spend on X'.",
            input_schema={"type": "object", "properties": {
                "date_from": {"type": "string", "format": "date"}, "date_to": {"type": "string", "format": "date"},
                "group_by": {"type": "string", "enum": ["category", "merchant", "month"]},
                "category": {"type": "string", "enum": _SPENDING_SLUGS}},
                "required": ["date_from", "date_to"]},
            run=_spending),
    AskTool(name="financials_find_transactions",
            description="Find individual bank/bill transactions whose payee or merchant contains the query text "
                        "(e.g. 'IMI', 'EDP'), newest first.",
            input_schema={"type": "object", "properties": {
                "query": {"type": "string"}, "date_from": {"type": "string", "format": "date"},
                "date_to": {"type": "string", "format": "date"}}, "required": ["query"]},
            run=_find_transactions),
)
