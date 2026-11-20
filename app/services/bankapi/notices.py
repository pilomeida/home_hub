"""The bank links' state in words: one row per bank and owner (the bank page and the Settings summary), and the
banners the Financials pages show: an access that has expired or soon will, and accounts that haven't updated (each
as a three-part message, app/problems.py)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from app.models.bank import BankAccountLink, BankConnection, BankConnectionStatus
from app.models.schedule import ProblemEntry
from app.problems import Problem
from app.services.bankapi import messages as msg
from app.services.bankapi.connect import (
    DEFAULT_OWNER, SUPPORTED_BANKS, connection_days_left, connection_label, consent_ended, consent_warning,
    latest_connections, owner_of,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def bank_notices(session: Session, now: Optional[datetime] = None) -> list[Problem]:
    """What/why/what-to-do banners for the Financials pages: an access that has expired or soon will,
    and accounts that haven't updated."""
    now = now or _utcnow()
    notices: list[Problem] = []
    latest = latest_connections(session)
    for conn in latest.values():
        warning = consent_warning(conn, now)
        if warning is not None:
            notices.append(warning)
    for link in session.exec(select(BankAccountLink)).all():
        link_conn = session.get(BankConnection, link.connection_id)
        # Only the current connection's links can be stale; after a renewal
        # the superseded connection must stay silent.
        key = (link_conn.bank_name, link_conn.owner or DEFAULT_OWNER) if link_conn else None
        if link_conn is None or latest.get(key) is None \
                or latest[key].id != link.connection_id \
                or link_conn.status != BankConnectionStatus.ACTIVE:
            continue
        account = msg.label(connection_label(link_conn), link.display_name)
        if link.last_error:
            notices.append(_failure_notice(session, link, account, connection_label(link_conn)))
        elif link.last_synced_at is not None and (now - link.last_synced_at) > timedelta(days=2):
            notices.append(msg.not_updated_for_days(account, link.last_synced_at))
    seen = set()
    unique = []
    for n in notices:
        if n.text not in seen:
            seen.add(n.text)
            unique.append(n)
    return unique


def _failure_notice(session: Session, link: BankAccountLink, account: str, bank_name: str) -> Problem:
    """The reason this account failed, in the words the sync logged on the Problems page."""
    entry = session.exec(select(ProblemEntry).where(
        ProblemEntry.ref == msg.link_ref(link.id), ProblemEntry.area == msg.AREA,
        ProblemEntry.resolved_at == None).order_by(ProblemEntry.at.desc())).first()   # noqa: E711
    if entry is not None:
        return Problem(entry.what, entry.why, entry.next_step, entry.link)
    return msg.last_attempt_failed(account, bank_name)


def connection_rows(session: Session, now: datetime) -> list[dict]:
    """One row per supported bank and owner: its state in words, days left, last update, the hint to show. Shared by
    this page and the Settings summary (step 10f)."""
    by_bank = latest_connections(session)
    links = session.exec(select(BankAccountLink)).all()
    rows = []
    for bank_key, (bank_name, _country) in SUPPORTED_BANKS.items():
        owner = owner_of(bank_key)
        label = bank_name if owner == DEFAULT_OWNER else f"{bank_name} ({owner})"
        conn = by_bank.get((bank_name, owner))
        bank_links = [l for l in links if conn and l.connection_id == conn.id]
        mapped = [l for l in bank_links if l.account_id is not None]
        days = connection_days_left(conn) if conn else None
        hint: Optional[Problem] = None
        if conn is None or conn.status == BankConnectionStatus.FAILED:
            status_label = "Not connected"
        elif conn.status == BankConnectionStatus.PENDING:
            status_label = "Waiting for you to approve in the bank's app"
            hint = msg.waiting_for_approval(bank_name)
        elif conn.status == BankConnectionStatus.EXPIRED or consent_ended(conn, now):
            status_label = "Access has ended"
            hint = msg.consent_expired(bank_name)
        elif len(mapped) < len(bank_links):
            status_label = "Needs account mapping"
            hint = msg.account_not_linked(bank_name)
        else:
            status_label = "Connected"
        last_update = max((l.last_synced_at for l in bank_links if l.last_synced_at), default=None)
        errors = [l.last_error for l in bank_links if l.last_error]
        rows.append({
            "key": bank_key, "name": label, "connection": conn,
            "status": status_label, "days_left": days, "last_update": last_update,
            "links": bank_links, "hint": hint,
            # An ended access explains itself through the hint; the stored sync error only adds noise there.
            "error": errors[0] if errors and hint is None else None,
        })
    return rows
