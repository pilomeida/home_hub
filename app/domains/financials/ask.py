"""Financials' contribution to Ask: spend aggregates and transaction search.
Same scoping as the Overview (paid_date, DEBIT only)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from urllib.parse import urlencode

from sqlmodel import Session, select

from app.models.merchant import Merchant
from app.models.transaction import Category, Transaction, TransactionType
from app.services.ask.contracts import AskTool, Citable, ToolOutput

_TOP_N = 25


def _txn_url(**params) -> str:
    return "/financials/transactions?" + urlencode({k: v for k, v in params.items() if v not in (None, "")})


def _merchant_names(session: Session) -> dict[int, str]:
    return {m.id: m.canonical_name for m in session.exec(select(Merchant)).all()}


def _spending(session: Session, args: dict) -> ToolOutput:
    date_from, date_to = date.fromisoformat(args["date_from"]), date.fromisoformat(args["date_to"])
    group_by, category = args.get("group_by", "category"), args.get("category")
    statement = select(Transaction).where(Transaction.transaction_type == TransactionType.DEBIT,
                                          Transaction.paid_date >= date_from, Transaction.paid_date <= date_to)
    if category:
        statement = statement.where(Transaction.category == Category(category))
    merchants = _merchant_names(session) if group_by == "merchant" else {}
    totals: dict[str, list] = defaultdict(lambda: [0.0, 0])
    for t in session.exec(statement).all():
        key = (merchants.get(t.merchant_id, t.provider) if group_by == "merchant"
               else f"{t.paid_date:%Y-%m}" if group_by == "month" else t.category.value)
        totals[key][0] += t.amount
        totals[key][1] += 1
    ordered = sorted(totals.items()) if group_by == "month" else sorted(totals.items(), key=lambda kv: -kv[1][0])
    lines = [f"Spending (debits, by paid date) {date_from}–{date_to}, grouped by {group_by}"
             + (f", category {category}" if category else "") + f": total €{sum(v[0] for v in totals.values()):,.2f}"]
    lines += [f"- {k}: €{v[0]:,.2f} ({v[1]} payments)" for k, v in ordered[:_TOP_N]]
    citable = Citable(
        ref=f"financials:spend:{category or 'all'}:{date_from}:{date_to}:{group_by}",
        label=f"Transactions {date_from} to {date_to}" + (f" ({category})" if category else ""),
        url=_txn_url(transaction_type="debit", date_from=date_from.isoformat(), date_to=date_to.isoformat(),
                     category=category))
    return ToolOutput(text="\n".join(lines), citables=[citable], used_raw_sources=True)


def _find_transactions(session: Session, args: dict) -> ToolOutput:
    needle = (args.get("query") or "").lower()
    statement = select(Transaction)
    if args.get("date_from"):
        statement = statement.where(Transaction.paid_date >= date.fromisoformat(args["date_from"]))
    if args.get("date_to"):
        statement = statement.where(Transaction.paid_date <= date.fromisoformat(args["date_to"]))
    merchants = _merchant_names(session)
    matches = [t for t in session.exec(statement.order_by(Transaction.paid_date.desc())).all()
               if needle in t.provider.lower() or needle in merchants.get(t.merchant_id, "").lower()]
    shown = matches[:40]
    lines = [f"{len(matches)} matching transactions (showing {len(shown)}):"] + [
        f"financials:txn:{t.id} | {t.paid_date or '-'} | {merchants.get(t.merchant_id, t.provider)} | "
        f"{t.transaction_type.value} €{t.amount:,.2f} | {t.category.value}" for t in shown]
    citables = [Citable(ref=f"financials:txn:{t.id}", label=f"Transaction: {t.provider} ({t.paid_date})",
                        url=_txn_url(transaction_id=t.id)) for t in shown]
    return ToolOutput(text="\n".join(lines), citables=citables, used_raw_sources=True)


FINANCIALS_ASK_TOOLS: tuple[AskTool, ...] = (
    AskTool(name="financials_spending",
            description="Total household spending (debits, by paid date) over a date range, grouped by category, "
                        "merchant or month. Use for 'how much did we spend on X'.",
            input_schema={"type": "object", "properties": {
                "date_from": {"type": "string", "format": "date"}, "date_to": {"type": "string", "format": "date"},
                "group_by": {"type": "string", "enum": ["category", "merchant", "month"]},
                "category": {"type": "string", "enum": [c.value for c in Category]}},
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
