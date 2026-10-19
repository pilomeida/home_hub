"""The cash position of an account (Pedro, 2026-10-19): the last balance the bank itself reported, rolled
forward with every line the bank sync stored after that reading, until the next reading.

A line counts when it came from the bank feed (it has an external id), was stored after the reading, and was
not excluded as a twin of another line. Its sign is the bank's own (`cash_sign`), which survives a line being
filed as an internal transfer."""
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from app.models.bank import BankAccountLink
from app.models.transaction import Transaction, TransactionType


def _signed(t: Transaction) -> float:
    if t.cash_sign is not None:
        return t.cash_sign * t.amount
    if t.transaction_type == TransactionType.CREDIT:
        return t.amount
    if t.transaction_type == TransactionType.DEBIT:
        return -t.amount
    return 0.0          # a transfer stored before signs were kept: its direction is unknown


def movement_since(session: Session, account_id: int, since: datetime, until: Optional[datetime] = None) -> float:
    stmt = select(Transaction).where(
        Transaction.account_id == account_id, Transaction.external_id.is_not(None),
        Transaction.settled_by_id.is_(None), Transaction.created_at > since)
    if until is not None:
        stmt = stmt.where(Transaction.created_at <= until)
    return sum(_signed(t) for t in session.exec(stmt).all())


def anchor_for(session: Session, account_id: int) -> Optional[BankAccountLink]:
    """The link holding the newest bank reading of this account."""
    links = session.exec(select(BankAccountLink).where(
        BankAccountLink.account_id == account_id, BankAccountLink.balance.is_not(None),
        BankAccountLink.balance_at.is_not(None))).all()
    return max(links, key=lambda l: l.balance_at, default=None)


def cash_position(session: Session, account_id: int) -> Optional[tuple[float, datetime]]:
    """(balance now, time of the bank reading it rests on), or None until the bank has been asked."""
    link = anchor_for(session, account_id)
    if link is None:
        return None
    return round(link.balance + movement_since(session, account_id, link.balance_at), 2), link.balance_at
