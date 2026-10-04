"""Routes for connecting banks and mapping their accounts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session
from app.models.account import Account
from app.models.bank import BankAccountLink, BankConnection, BankConnectionStatus
from app.services.bankapi.client import BankApiError, EnableBankingClient
from app.services.bankapi.connect import (
    SUPPORTED_BANKS, begin_connection, complete_connection, connection_days_left,
)
from app.templating import templates

router = APIRouter(prefix="/financials/bank", tags=["bank"])


def get_bank_client() -> EnableBankingClient:
    if not settings.bank_configured:
        raise HTTPException(status_code=503, detail="Bank connections aren't set up on this server yet")
    return EnableBankingClient(settings.ENABLE_BANKING_APP_ID, Path(settings.ENABLE_BANKING_KEY_PATH).read_bytes())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.get("/")
async def connections_page(request: Request, session: Session = Depends(get_session)):
    connections = session.exec(select(BankConnection)).all()
    by_bank: dict[str, BankConnection] = {}
    for conn in connections:
        if conn.bank_name not in by_bank or conn.created_at > by_bank[conn.bank_name].created_at:
            by_bank[conn.bank_name] = conn
    links = session.exec(select(BankAccountLink)).all()
    rows = []
    for bank_key, (bank_name, _country) in SUPPORTED_BANKS.items():
        conn = by_bank.get(bank_name)
        bank_links = [l for l in links if conn and l.connection_id == conn.id]
        mapped = [l for l in bank_links if l.account_id is not None]
        days = connection_days_left(conn) if conn else None
        if conn is None or conn.status == BankConnectionStatus.FAILED:
            status_label = "Not connected"
        elif conn.status == BankConnectionStatus.PENDING:
            status_label = "Waiting for you to approve in the bank's app"
        elif conn.status == BankConnectionStatus.EXPIRED or (days is not None and days <= 0):
            status_label = "Access has ended"
        elif len(mapped) < len(bank_links):
            status_label = "Needs account mapping"
        else:
            status_label = "Connected"
        last_update = max((l.last_synced_at for l in bank_links if l.last_synced_at), default=None)
        errors = [l.last_error for l in bank_links if l.last_error]
        rows.append({
            "key": bank_key, "name": bank_name, "connection": conn,
            "status": status_label, "days_left": days, "last_update": last_update,
            "links": bank_links, "error": errors[0] if errors else None,
        })
    return templates.TemplateResponse(request, "bank/connections.html", {
        "rows": rows, "now": _utcnow(),
    })


@router.post("/connect/{bank_key}")
async def connect(bank_key: str, request: Request,
                  session: Session = Depends(get_session),
                  client: EnableBankingClient = Depends(get_bank_client)):
    public_base = settings.PUBLIC_BASE_URL.rstrip("/")
    try:
        url = await begin_connection(session, client, bank_key,
                                     redirect_url=f"{public_base}/financials/bank/callback")
    except BankApiError:
        return _friendly_error_page(
            request, "We couldn't start the bank connection.",
            "The bank didn't accept our request. Please try again in a moment; if it keeps happening, tell Pedro.",
        )
    return RedirectResponse(url, status_code=303)


@router.get("/callback")
async def callback(request: Request, state: str = "", code: str = "", error: str = "",
                   session: Session = Depends(get_session),
                   client: EnableBankingClient = Depends(get_bank_client)):
    if error:
        return _friendly_error_page(
            request,
            "The bank sent you back without finishing — nothing was connected. Try again when you're ready.",
        )
    try:
        connection = await complete_connection(session, client, state=state, code=code)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="That bank link has expired or was already used — start again",
        )
    except BankApiError:
        return _friendly_error_page(
            request,
            "The bank didn't finish the connection. Nothing was saved — try connecting again.",
        )
    unmapped = session.exec(
        select(BankAccountLink).where(
            BankAccountLink.connection_id == connection.id,
            BankAccountLink.account_id.is_(None),
        )
    ).first()
    if unmapped is not None:
        return RedirectResponse(f"/financials/bank/{connection.id}/map", status_code=303)
    return RedirectResponse("/financials/bank/", status_code=303)


def _friendly_error_page(request: Request, message: str, detail: str = ""):
    return templates.TemplateResponse(request, "bank/error.html", {
        "message": message, "detail": detail,
    })


@router.get("/{connection_id}/map")
async def map_page(request: Request, connection_id: int,
                   session: Session = Depends(get_session)):
    connection = session.get(BankConnection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="No such bank connection")
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
        raise HTTPException(status_code=404, detail="No such bank account on this connection")
    if raw:
        account = session.get(Account, int(raw))
        if account is None:
            raise HTTPException(status_code=400, detail="Pick one of the listed Hub accounts")
        link.account_id = account.id
    else:
        link.account_id = None  # "— not tracked —"
    session.add(link)
    session.commit()
    unmapped = session.exec(
        select(BankAccountLink).where(
            BankAccountLink.connection_id == connection_id,
            BankAccountLink.account_id.is_(None),
        )
    ).first()
    if unmapped is not None:
        return RedirectResponse(f"/financials/bank/{connection_id}/map", status_code=303)
    return RedirectResponse("/financials/bank/", status_code=303)
