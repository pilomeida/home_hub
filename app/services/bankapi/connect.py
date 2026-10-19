"""Authorize a bank through Enable Banking and map its accounts to Hub accounts."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from app.models.account import Account
from app.models.bank import BankAccountLink, BankConnection, BankConnectionStatus
from app.problems import resolve_for
from app.services.bankapi import messages as msg

# aspsp.name strings must be confirmed against GET /aspsps (verified 2026-10-05: "Santander Totta"/PT, "Revolut"/LT) and
# corrected here if they differ.
SUPPORTED_BANKS = {"santander": ("Santander Totta", "PT"), "revolut": ("Revolut", "LT"),
                   "santander-rute": ("Santander Totta", "PT")}
# Whose login a connect button asks for (default: Pedro). A bank access belongs to one person.
CONNECTION_OWNERS = {"santander-rute": "Rute"}
DEFAULT_OWNER = "Pedro"


def owner_of(bank_key: str) -> str:
    return CONNECTION_OWNERS.get(bank_key, DEFAULT_OWNER)


def connection_label(conn) -> str:
    """"Santander Totta" for Pedro's own, "Santander Totta (Rute)" for someone else's."""
    owner = conn.owner or DEFAULT_OWNER
    return conn.bank_name if owner == DEFAULT_OWNER else f"{conn.bank_name} ({owner})"


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
    connection = BankConnection(bank_name=bank_name, country=country, state=state, owner=owner_of(bank_key),
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
        carried_account_id = hub_account.id if hub_account else None
        carried_synced_at = None
        # Renewal: carry the mapping and sync history over from the most
        # recent earlier link of the same bank with the same IBAN (or the
        # same display name when the bank gives no IBAN).
        prior = session.exec(
            select(BankAccountLink, BankConnection)
            .join(BankConnection, BankConnection.id == BankAccountLink.connection_id)  # type: ignore[call-arg]
            .where(BankConnection.bank_name == connection.bank_name, BankConnection.owner == connection.owner,
                   BankConnection.id != connection.id,
                   BankAccountLink.iban == account.iban if account.iban
                   # No IBAN: only match another IBAN-less link. Name alone is not
                   # enough -- a person's current account and credit card share it.
                   else (BankAccountLink.display_name == account.name)
                   & BankAccountLink.iban.is_(None))  # type: ignore[arg-type,attr-defined]
            .order_by(BankConnection.created_at.desc())  # type: ignore[attr-defined]
        ).first()
        if prior is not None:
            prior_link = prior[0]
            carried_account_id = prior_link.account_id if carried_account_id is None else carried_account_id
            carried_synced_at = prior_link.last_synced_at
        session.add(BankAccountLink(
            connection_id=connection.id, bank_account_uid=account.uid,
            iban=account.iban, display_name=account.name,
            account_id=carried_account_id, last_synced_at=carried_synced_at,
        ))
    # Supersede: the job must never sync the dead session after a renewal.
    for other in session.exec(
        select(BankConnection).where(
            BankConnection.bank_name == connection.bank_name, BankConnection.owner == connection.owner,
            BankConnection.id != connection.id,
            BankConnection.status == BankConnectionStatus.ACTIVE,
        )
    ).all():
        other.status = BankConnectionStatus.EXPIRED
        session.add(other)
    session.commit()
    # Renewed: the "access expired / expires soon" entries in the Problems log for this bank are over, and
    # so are the ones for its accounts (their next sync starts clean on the new connection).
    for old in session.exec(select(BankConnection).where(
            BankConnection.bank_name == connection.bank_name, BankConnection.owner == connection.owner,
            BankConnection.id != connection.id)).all():
        resolve_for(session, msg.connection_ref(old.id), by="renewed")
        resolve_for(session, msg.expiring_ref(old.id), by="renewed")
        for old_link in session.exec(select(BankAccountLink).where(BankAccountLink.connection_id == old.id)).all():
            resolve_for(session, msg.link_ref(old_link.id), by="renewed")
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


def consent_ended(conn: BankConnection, now: Optional[datetime] = None) -> bool:
    """True when the bank's permission is over: marked expired, or its end time has passed."""
    if conn.status == BankConnectionStatus.EXPIRED:
        return True
    if conn.valid_until is None:
        return False
    now = (now or _utcnow()).replace(tzinfo=None)
    return conn.valid_until.replace(tzinfo=None) <= now


def consent_warning(conn: BankConnection, now: Optional[datetime] = None):
    """The message for a bank permission that is over or about to be (14 days or less), else None.
    Only the latest connection of a bank should be asked: a superseded one stays silent."""
    if conn.status in (BankConnectionStatus.PENDING, BankConnectionStatus.FAILED):
        return None
    if consent_ended(conn, now):
        return msg.consent_expired(conn.bank_name)
    days = connection_days_left(conn, now=now)
    if days is not None and days <= 14:
        return msg.consent_expiring(conn.bank_name, days)
    return None


def latest_connections(session: Session) -> dict[tuple[str, str], BankConnection]:
    """The current connection of each bank and person, keyed (bank name, owner): the newest one."""
    latest: dict[tuple[str, str], BankConnection] = {}
    for conn in session.exec(select(BankConnection)).all():
        key = (conn.bank_name, conn.owner or DEFAULT_OWNER)
        if key not in latest or conn.created_at > latest[key].created_at:
            latest[key] = conn
    return latest
