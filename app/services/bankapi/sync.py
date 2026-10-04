"""Pull booked bank transactions into the Hub's Transaction table."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlmodel import Session, select

from app.models.bank import BankAccountLink, BankApiCall, BankConnection, BankConnectionStatus
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.merchant import Merchant
from app.models.transaction import Category, Transaction, TransactionType
from app.services.bankapi.client import BankApiError
from app.services.classification_engine import classify_transaction

DAILY_CALL_LIMIT = 4
SCHEDULED_CALL_LIMIT = 3
_OVERLAP_DAYS = 2


@dataclass
class SyncResult:
    fetched: int = 0
    created: int = 0
    matched_existing: int = 0
    already_synced: int = 0
    skipped_quota: bool = False
    error: Optional[str] = None


def sync_document(session: Session, account_id: int, bank_name: str) -> Document:
    key = f"bank-sync:{account_id}"
    doc = session.exec(select(Document).where(Document.content_hash == key)).first()
    if doc is None:
        doc = Document(
            filename=f"{bank_name} — automatic bank sync", file_path="", content_hash=key,
            source=DocumentSource.API, status=DocumentStatus.PROCESSED,
            domain=Domain.FINANCIALS, category="statement", account_id=account_id,
        )
        session.add(doc)
        session.flush()
    return doc


def _calls_last_24h(session: Session, link_id: int, now: datetime) -> int:
    since = now - timedelta(hours=24)
    rows = session.exec(
        select(BankApiCall).where(BankApiCall.link_id == link_id, BankApiCall.called_at >= since)
    ).all()
    return len(rows)


def _window_start(session: Session, link: BankAccountLink, today: date) -> date:
    if link.last_synced_at is not None:
        return link.last_synced_at.date() - timedelta(days=3)
    latest = session.exec(
        select(Transaction.paid_date)
        .where(Transaction.account_id == link.account_id, Transaction.paid_date.is_not(None))  # type: ignore[attr-defined]
        .order_by(Transaction.paid_date.desc())  # type: ignore[attr-defined]
    ).first()
    return (latest - timedelta(days=3)) if latest else today - timedelta(days=85)


def _external_id(raw: dict) -> str:
    ref = raw.get("entry_reference") or raw.get("transaction_id")
    if ref:
        return str(ref)
    amount = raw.get("transaction_amount") or {}
    basis = "|".join([
        str(raw.get("booking_date")), str(amount.get("amount")), str(amount.get("currency")),
        str(raw.get("credit_debit_indicator")), " ".join(raw.get("remittance_information") or []),
    ])
    return hashlib.sha256(basis.encode()).hexdigest()


def _provider(raw: dict, is_credit: bool) -> str:
    party = (raw.get("debtor") if is_credit else raw.get("creditor")) or {}
    return party.get("name") or " ".join(raw.get("remittance_information") or []).strip() or "Unknown"


def _matches_existing(session: Session, account_id: int, kind: TransactionType, amount: float, paid: date) -> bool:
    lo, hi = paid - timedelta(days=_OVERLAP_DAYS), paid + timedelta(days=_OVERLAP_DAYS)
    candidates = session.exec(
        select(Transaction).where(
            Transaction.account_id == account_id, Transaction.external_id.is_(None),  # type: ignore[attr-defined]
            Transaction.transaction_type == kind, Transaction.paid_date >= lo, Transaction.paid_date <= hi,
        )
    ).all()
    return any(round(c.amount, 2) == round(amount, 2) for c in candidates)


def _friendly_failure(exc: BankApiError) -> str:
    if exc.rate_limited:
        return "The bank asked us to wait; will retry later"
    return "The bank didn't answer properly; will retry later"


async def sync_link(session: Session, client, link: BankAccountLink, *, dry_run: bool = False,
                    scheduled: bool = True, today: Optional[date] = None,
                    classify=classify_transaction) -> SyncResult:
    result = SyncResult()
    now = datetime.utcnow()
    today = today or now.date()
    limit = SCHEDULED_CALL_LIMIT if scheduled else DAILY_CALL_LIMIT
    if _calls_last_24h(session, link.id, now) >= limit:
        result.skipped_quota = True
        return result
    connection = session.get(BankConnection, link.connection_id)
    date_from = _window_start(session, link, today)

    session.add(BankApiCall(link_id=link.id, called_at=now, kind="transactions"))
    session.commit()  # the call is spent even if it fails
    try:
        rows = await client.list_transactions(link.bank_account_uid, date_from, today)
    except BankApiError as exc:
        if exc.status in (401, 403) or "SESSION" in exc.code.upper() or "CONSENT" in exc.code.upper():
            connection.status = BankConnectionStatus.EXPIRED
            session.add(connection)
            result.error = "Bank access has ended; renew it"
        else:
            result.error = _friendly_failure(exc)
        link.last_error = result.error
        session.add(link)
        session.commit()
        return result

    result.fetched = len(rows)
    try:
        document = sync_document(session, link.account_id, connection.bank_name)
        for raw in rows:
            external_id = _external_id(raw)
            exists = session.exec(select(Transaction).where(
                Transaction.account_id == link.account_id, Transaction.external_id == external_id)).first()
            if exists is not None:
                result.already_synced += 1
                continue
            amount_info = raw.get("transaction_amount") or {}
            is_credit = raw.get("credit_debit_indicator") == "CRDT"
            kind = TransactionType.CREDIT if is_credit else TransactionType.DEBIT
            amount = float(abs(Decimal(str(amount_info.get("amount", "0")))))
            paid = date.fromisoformat(raw.get("booking_date") or raw["value_date"])
            if _matches_existing(session, link.account_id, kind, amount, paid):
                result.matched_existing += 1
                continue
            result.created += 1
            if dry_run:
                continue
            txn = Transaction(
                document_id=document.id, provider=_provider(raw, is_credit), category=Category.OTHER,
                transaction_type=kind, amount=amount, currency=amount_info.get("currency", "EUR"),
                paid_date=paid, statement_period=paid.strftime("%Y-%m"),
                account_id=link.account_id, external_id=external_id,
            )
            session.add(txn)
            session.flush()
            await classify(session, txn)
            if txn.merchant_id is not None:
                merchant = session.get(Merchant, txn.merchant_id)
                if merchant is not None and merchant.default_category != Category.OTHER:
                    txn.category = merchant.default_category
        if not dry_run:
            link.last_synced_at = now
            link.last_error = None
            session.add(link)
        session.commit()
    except Exception:
        session.rollback()
        result.error = "Something went wrong saving the bank transactions; nothing was saved"
        link = session.get(BankAccountLink, link.id)
        link.last_error = result.error
        session.add(link)
        session.commit()
        result.created = 0
    return result
