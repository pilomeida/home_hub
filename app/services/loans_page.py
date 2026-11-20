"""What the Loans & Savings pages show: one row per loan (balance, payoff, alerts, linked insurance), the overview
(active and closed loans, informal debts, funds by holder, deposits, cards), a loan's history and rates, and the
lookups the statement upload needs."""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.baseline import BASELINE_DATE
from app.models.category_node import CategoryNode
from app.models.debt import Debt
from app.models.document import Document
from app.models.person import Person
from app.models.position import LoanAlert, LoanMovement, LoanSnapshot, PositionExtraction
from app.models.transaction import Transaction, TransactionType
from app.services.loan_alerts import SPREAD_TOLERANCE, group_alerts
from app.services.loan_insurance import LOAN_INSURANCE_SLUGS
from app.services.loan_math import (
    LoanSummary, informal_debt_rows, informal_summary, newest_balances, savings_lines, summarize_all,
)


@dataclass
class LoanRow:
    summary: LoanSummary
    from_statements: bool
    rate_derived: bool
    remaining_pct: Optional[float]
    payoff_text: Optional[str]
    alerts: list = field(default_factory=list)  # unacknowledged LoanAlert rows
    groups: list = field(default_factory=list)  # the same, grouped per kind (AlertGroup)
    acked_alerts: list = field(default_factory=list)
    insurance_paid: float = 0.0  # linked insurance debits (SEG...), beside the instalment
    last_premium: Optional[float] = None
    last_premium_date: Optional[date] = None


def payoff_text(months_left: Optional[int], payoff_date: Optional[date]) -> Optional[str]:
    if months_left is None or payoff_date is None:
        return None
    years, months = divmod(months_left, 12)
    parts = []
    if years:
        parts.append(f"{years} yr{'s' if years != 1 else ''}")
    if months or not years:
        parts.append(f"{months} mo")
    return f"≈ {' '.join(parts)} ({payoff_date.strftime('%Y-%m')})"


def insurance_debits(session: Session, debt_id: int) -> tuple[float, Optional[float], Optional[date]]:
    """(total, latest premium, its date) of the debits linked to the loan AND filed
    under the Loan insurance nodes."""
    node_ids = session.exec(select(CategoryNode.id).where(CategoryNode.slug.in_(LOAN_INSURANCE_SLUGS))).all()
    if not node_ids:
        return 0.0, None, None
    rows = session.exec(
        select(Transaction).where(
            Transaction.debt_id == debt_id, Transaction.transaction_type == TransactionType.DEBIT,
            Transaction.counts(), Transaction.category_id.in_(node_ids),
        ).order_by(Transaction.paid_date, Transaction.id)
    ).all()
    if not rows:
        return 0.0, None, None
    return round(sum(t.amount for t in rows), 2), rows[-1].amount, rows[-1].paid_date


def loan_row(session: Session, summary: LoanSummary) -> LoanRow:
    snapshot = session.exec(
        select(LoanSnapshot).where(LoanSnapshot.debt_id == summary.debt_id).order_by(LoanSnapshot.as_of.desc())
    ).first()
    pct = None
    if summary.balance_known and summary.capital_granted:
        pct = max(0.0, min(100.0, summary.capital_remaining / summary.capital_granted * 100))
    alerts = session.exec(
        select(LoanAlert).where(LoanAlert.debt_id == summary.debt_id).order_by(LoanAlert.detected_on, LoanAlert.id)
    ).all()
    paid, premium, premium_date = insurance_debits(session, summary.debt_id)
    debt = session.get(Debt, summary.debt_id)
    return LoanRow(
        summary=summary,
        insurance_paid=paid, last_premium=premium, last_premium_date=premium_date,
        alerts=[a for a in alerts if not a.acknowledged],
        groups=group_alerts([(a, debt) for a in alerts if not a.acknowledged]) if alerts else [],
        acked_alerts=[a for a in alerts if a.acknowledged],
        from_statements=snapshot is not None,
        rate_derived=summary.rate_source in ("derived", "debt"),
        remaining_pct=pct,
        payoff_text=payoff_text(summary.months_left, summary.payoff_date),
    )


def overview(session: Session, today: date) -> dict:
    """The Loans & Savings page's lists."""
    summaries = summarize_all(session, today)
    rows = [loan_row(session, s) for s in summaries]
    active = [r for r in rows if r.summary.capital_remaining > 0 or not r.summary.balance_known]
    closed = [r for r in rows if r not in active]
    people = {p.id: p.name for p in session.exec(select(Person)).all()}
    informal = [
        {"debt": d, "balance": float(balance), "person": people.get(d.person_id), "summary": informal_summary(session, d)}
        for d, balance in informal_debt_rows(session)
    ]
    funds = savings_lines(session)
    holders: dict[str, list] = {}
    for line in funds:
        holders.setdefault(line.holder or "Other", []).append(line)
    deposits = newest_balances(session, "deposit")
    cards = newest_balances(session, "card")
    return {"rows": rows, "active": active, "closed": closed, "informal": informal, "funds": funds,
            "holders": holders, "deposits": deposits, "cards": cards}


def loan_history(session: Session, debt: Debt) -> dict:
    """A loan's movements since the baseline (newest first), its balance strip, and its rate history."""
    movements = session.exec(
        select(LoanMovement).where(LoanMovement.debt_id == debt.id, LoanMovement.movement_date >= BASELINE_DATE)
        .order_by(LoanMovement.movement_date.desc(), LoanMovement.instalment_number.desc())
    ).all()
    series = [m.balance_after for m in reversed(movements) if m.balance_after is not None]
    top = max(series) if series else 0
    strip = [{"value": v, "pct": round(v / top * 100) if top else 0} for v in series]
    snapshots = session.exec(
        select(LoanSnapshot).where(LoanSnapshot.debt_id == debt.id).order_by(LoanSnapshot.as_of)
    ).all()
    rate_history = [
        {"as_of": sn.as_of, "indexante": sn.indexante_percent, "spread": sn.spread_percent,
         "tan": sn.next_rate_percent if sn.next_rate_percent is not None else sn.rate_percent,
         "spread_changed": (sn.spread_percent is not None and debt.spread_percent is not None
                            and abs(sn.spread_percent - debt.spread_percent) > SPREAD_TOLERANCE)}
        for sn in snapshots
    ]
    return {"movements": movements, "strip": strip, "rate_history": rate_history}


def extraction_for(session: Session, document: Document) -> Optional[PositionExtraction]:
    session.expire_all()
    return session.exec(select(PositionExtraction).where(PositionExtraction.document_id == document.id)).first()


def existing_loans(session: Session) -> list[Debt]:
    return list(session.exec(select(Debt).where(Debt.external_number.isnot(None)).order_by(Debt.name)).all())


def loan_by_number(session: Session, digits: str) -> Optional[Debt]:
    return session.exec(select(Debt).where(Debt.external_number == digits)).first()
