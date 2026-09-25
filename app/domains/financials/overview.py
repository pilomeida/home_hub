"""Financials' card on the cross-domain '/' Overview."""

from datetime import date

from sqlmodel import Session, func, select

from app.domains.base import CardLine, DomainCard
from app.models.document import Document, DocumentStatus
from app.models.domain import Domain
from app.models.transaction import Transaction, TransactionType


def financials_overview_card(session: Session, today: date) -> DomainCard:
    month_start = today.replace(day=1)
    spent = session.exec(
        select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
            Transaction.transaction_type == TransactionType.DEBIT,
            Transaction.paid_date >= month_start,
            Transaction.paid_date <= today,
        )
    ).one()
    needing_attention = session.exec(
        select(func.count()).select_from(Document).where(
            Document.domain == Domain.FINANCIALS, Document.status == DocumentStatus.NEEDS_ATTENTION,
        )
    ).one()

    lines = [CardLine(
        text=f"Spent this month: €{spent:,.2f}",
        url=f"/financials/transactions?transaction_type=debit&date_from={month_start.isoformat()}&date_to={today.isoformat()}",
    )]
    if needing_attention:
        lines.append(CardLine(
            text=f"{needing_attention} document{'s' if needing_attention != 1 else ''} need{'s' if needing_attention == 1 else ''} attention",
            url="/financials/bills",
            attention=True,
        ))
    return DomainCard(label="Financials", url="/financials/bills", lines=lines)