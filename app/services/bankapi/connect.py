"""Authorize a bank through Enable Banking and map its accounts to Hub accounts."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from app.models.account import Account
from app.models.bank import BankAccountLink, BankConnection, BankConnectionStatus

# aspsp.name strings must be confirmed against GET /aspsps (Task 7/8) and
# corrected here if they differ.
SUPPORTED_BANKS = {"santander": ("Santander", "PT"), "revolut": ("Revolut", "LT")}

_MAX_CONSENT_DAYS = 180


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def begin_connection(session: Session, client, bank_key: str, *,
                           redirect_url: str) -> str:
    if bank_key not in SUPPORTED_BANKS:
        raise ValueError(f"Unsupported bank: {bank_key}")
    bank_name, country = SUPPORTED_BANKS[bank_key]
    state = secrets.token_urlsafe(24)
    days = min(await client.max_consent_days(bank_name, country), _MAX_CONSENT_DAYS)
    connection = BankConnection(bank_name=bank_name, country=country, state=state,
                                status=BankConnectionStatus.PENDING)
    session.add(connection)
    session.commit()
    try:
        start = await client.start_auth(
            bank_name=bank_name, country=country,
            valid_until=_utcnow() + timedelta(days=days),
            state=state, redirect_url=redirect_url,
        )
    except Exception:
        session.delete(connection)
        session.commit()
        raise
    return start.url


async def complete_connection(session: Session, client, *, state: str, code: str) -> BankConnection:
    connection = session.exec(
        select(BankConnection).where(BankConnection.state == state)
    ).first()
    if connection is None or connection.status != BankConnectionStatus.PENDING:
        raise ValueError("unknown or already-used connection state")
    bank_session = await client.create_session(code)
    connection.status = BankConnectionStatus.ACTIVE
    connection.session_id = bank_session.session_id
    connection.valid_until = bank_session.valid_until
    connection.authorized_at = _utcnow()
    session.add(connection)
    for account in bank_session.accounts:
        hub_account = session.exec(
            select(Account).where(Account.identifier == account.iban)
        ).first() if account.iban else None
        session.add(BankAccountLink(
            connection_id=connection.id, bank_account_uid=account.uid,
            iban=account.iban, display_name=account.name,
            account_id=hub_account.id if hub_account else None,
        ))
    session.commit()
    session.refresh(connection)
    return connection


def map_account(session: Session, link_id: int, account_id: int) -> None:
    link = session.get(BankAccountLink, link_id)
    if link is None:
        raise ValueError(f"no such bank account link: {link_id}")
    link.account_id = account_id
    session.add(link)
    session.commit()


def connection_days_left(conn: BankConnection, now: Optional[datetime] = None) -> Optional[int]:
    if conn.valid_until is None:
        return None
    now = now or _utcnow()
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    valid_until = conn.valid_until
    if valid_until.tzinfo is not None:
        valid_until = valid_until.replace(tzinfo=None)
    return max((valid_until - now).days, 0)
