"""Routes for connecting banks and mapping their accounts."""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session
from app.models.account import Account
from app.models.bank import BankAccountLink, BankConnection, BankConnectionStatus
from app.models.schedule import ProblemEntry
from app.problems import Problem, record_problem
from app.services.bankapi import messages as msg
from app.services.bankapi.client import BankApiError, EnableBankingClient
from app.services.bankapi.connect import (
    SUPPORTED_BANKS, begin_connection, complete_connection, connection_days_left, connection_label, consent_ended,
    consent_warning, feeding_elsewhere, latest_connections, owner_of, DEFAULT_OWNER,
)
from app.services.bankapi.sync import sync_link
from app.services.presentation import lisbon_datetime
from app.templating import templates

router = APIRouter(prefix="/financials/bank", tags=["bank"])
logger = logging.getLogger("bank")


def get_bank_client() -> EnableBankingClient:
    if not settings.bank_configured:
        raise HTTPException(status_code=503, detail=msg.bank_not_set_up())
    return EnableBankingClient(settings.ENABLE_BANKING_APP_ID, Path(settings.ENABLE_BANKING_KEY_PATH).read_bytes())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.get("/")
async def connections_page(request: Request, session: Session = Depends(get_session)):
    by_bank = latest_connections(session)
    links = session.exec(select(BankAccountLink)).all()
    now = _utcnow()
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
    return templates.TemplateResponse(request, "bank/connections.html", {
        "rows": rows, "now": now, "notices": bank_notices(session),
    })


@router.post("/connect/{bank_key}")
async def connect(bank_key: str, request: Request,
                  session: Session = Depends(get_session),
                  client: EnableBankingClient = Depends(get_bank_client)):
    public_base = settings.PUBLIC_BASE_URL.rstrip("/")
    try:
        url = await begin_connection(session, client, bank_key,
                                     redirect_url=f"{public_base}/financials/bank/callback")
    except ValueError as exc:
        return _friendly_error_page(request, msg.unknown_bank(), technical=str(exc), status_code=400)
    except (BankApiError, httpx.HTTPError) as exc:
        reached = isinstance(exc, BankApiError)
        reason = ("The bank said it couldn't start the connection right now." if reached
                  else "The Hub couldn't reach the bank to start the connection.")
        return _friendly_error_page(request, msg.connect_not_started(reason), technical=str(exc) or repr(exc))
    return RedirectResponse(url, status_code=303)


@router.get("/callback")
async def callback(request: Request, state: str = "", code: str = "", error: str = "",
                   session: Session = Depends(get_session),
                   client: EnableBankingClient = Depends(get_bank_client)):
    if error:
        return _friendly_error_page(request, msg.returned_without_finishing(), technical=f"The bank returned: {error}")
    try:
        connection = await complete_connection(session, client, state=state, code=code)
    except ValueError as exc:
        return _friendly_error_page(request, msg.link_used_or_expired(), technical=str(exc), status_code=400)
    except (BankApiError, httpx.HTTPError) as exc:
        return _friendly_error_page(request, msg.connection_not_finished(), technical=str(exc) or repr(exc))
    unmapped = session.exec(
        select(BankAccountLink).where(
            BankAccountLink.connection_id == connection.id,
            BankAccountLink.account_id.is_(None),
        )
    ).first()
    if unmapped is not None:
        return RedirectResponse(f"/financials/bank/{connection.id}/map", status_code=303)
    return RedirectResponse("/financials/bank/", status_code=303)


def _friendly_error_page(request: Request, problem: Problem, technical: str = "", status_code: int = 200):
    return templates.TemplateResponse(request, "bank/error.html", {
        "problem": problem, "technical": technical,
    }, status_code=status_code)


def _no_such_connection() -> dict:
    return {"what": "Couldn't open that bank connection.",
            "why": "The Hub has no bank connection with that number; the link may be out of date.",
            "next_step": "Go back to Bank connections and choose it from there.", "link": msg.BANK_PAGE}


@router.get("/{connection_id}/map")
async def map_page(request: Request, connection_id: int,
                   session: Session = Depends(get_session)):
    connection = session.get(BankConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail=_no_such_connection())
    links = session.exec(
        select(BankAccountLink).where(BankAccountLink.connection_id == connection_id)
    ).all()
    accounts = session.exec(select(Account)).all()
    return templates.TemplateResponse(request, "bank/map_accounts.html", {
        "connection": connection, "links": links, "accounts": accounts,
    })


@router.post("/{connection_id}/map/{link_id}")
async def map_one(connection_id: int, link_id: int, request: Request,
                  session: Session = Depends(get_session)):
    form = await request.form()
    raw = form.get("account_id")
    link = session.get(BankAccountLink, link_id)
    if link is None or link.connection_id != connection_id:
        raise HTTPException(status_code=404, detail={
            "what": "Couldn't open that bank account.",
            "why": "It isn't one of the accounts on this bank connection; the page may be out of date.",
            "next_step": "Go back to Bank connections and open “Map accounts” again.", "link": msg.BANK_PAGE})
    if raw:
        try:
            account = session.get(Account, int(raw))
        except (TypeError, ValueError):
            account = None
        if account is None:
            return _friendly_error_page(request, msg.unknown_account_choice(connection_id), status_code=400)
        other = feeding_elsewhere(session, link, account.id)
        if other is not None:
            via = msg.label(session.get(BankConnection, other.connection_id).bank_name, other.display_name)
            return _friendly_error_page(request, msg.already_fed(connection_id, account.name, via), status_code=400)
        link.account_id = account.id
    else:
        link.account_id = None  # "— not tracked —"
    session.add(link)
    session.commit()
    # Always back to the connections page: choosing "— not tracked —" must
    # not loop back to the same map page.
    return RedirectResponse("/financials/bank/", status_code=303)


@router.post("/{connection_id}/sync")
async def sync_now(connection_id: int, request: Request,
                   session: Session = Depends(get_session),
                   client: EnableBankingClient = Depends(get_bank_client)):
    connection = session.get(BankConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail=_no_such_connection())
    links = session.exec(
        select(BankAccountLink).where(
            BankAccountLink.connection_id == connection_id,
            BankAccountLink.account_id.is_not(None),  # type: ignore[attr-defined]
        )
    ).all()
    if not links:
        return _back(msg.nothing_linked(connection.bank_name).text)
    created = already = synced = 0
    problems: list[Problem] = []
    for link in links:
        account = msg.label(connection.bank_name, link.display_name)
        try:
            result = await sync_link(session, client, link, scheduled=False)
        except OperationalError:
            session.rollback()
            return _back(msg.hub_busy().text)
        except Exception as exc:        # never a bare 500 on a button press; the Problems page keeps the details
            session.rollback()
            logger.exception("Sync now failed for link %s", link.id)
            problem = msg.unexpected_failure(account)
            try:
                record_problem(session, area=msg.AREA, problem=problem, ref=msg.link_ref(link.id),
                               technical=traceback.format_exc())
            except Exception:
                logger.exception("could not record the sync problem")
            problems.append(problem)
            continue
        if result.problem is not None:
            problems.append(result.problem)
        else:
            synced += 1
            created += result.created
            already += result.already_synced + result.matched_existing
    parts = []
    if synced or not problems:
        parts.append(f"{created} new transaction{'s' if created != 1 else ''}. {already} already there.")
    parts.extend(dict.fromkeys(p.text for p in problems))   # each distinct message once
    return _back(" ".join(parts))


def _back(message: str):
    return RedirectResponse(f"/financials/bank/?msg={quote_plus(message)}", status_code=303)


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


def _notices_for_request() -> list[str]:
    """Banners for Financials pages rendered outside app.routers.bank use the
    context `notices` key passed by their router instead."""
    return []
