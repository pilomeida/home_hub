"""Pull booked bank transactions into the Hub's Transaction table."""

from __future__ import annotations

import hashlib
import logging
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx
from sqlmodel import Session, select

from app.models.bank import BankAccountLink, BankApiCall, BankConnection, BankConnectionStatus
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.merchant import Merchant
from app.models.transaction import Category, Transaction, TransactionType
from app.problems import Problem, record_problem, resolve_for
from app.services.bankapi import messages as msg
from app.services.bankapi.client import BankApiError
from app.services.document_reconcile import reconcile_quietly
from app.services.classification_engine import classify_transaction

logger = logging.getLogger(__name__)

DAILY_CALL_LIMIT = 4
# The bank's hard cap is 4 calls per account per 24 h — that is the only real
# limit. The 3-per-day timer cadence (07:30/13:00/18:30) is what leaves
# headroom for a manual "Sync now"; raising this constant to anything below 4
# would skip the morning run whenever timer jitter keeps yesterday's 07:30
# call inside the window.
SCHEDULED_CALL_LIMIT = DAILY_CALL_LIMIT
_OVERLAP_DAYS = 2


@dataclass
class SyncResult:
    fetched: int = 0
    created: int = 0
    matched_existing: int = 0
    already_synced: int = 0
    skipped_rows: int = 0
    unclassified: int = 0
    skipped_quota: bool = False
    error: Optional[str] = None          # the full what/why/next text when something failed (also stored on the link)
    problem: Optional[Problem] = None    # the same, structured; set for a failure and for a daily-limit stop
    _used_match_ids: set = field(default_factory=set, repr=False)


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
    # First sync. Start just before where the imported bank STATEMENTS end, so the
    # overlap is matched against them and nothing after them is missed. Rows the
    # sync itself wrote (external_id set) and bills (which can carry a later date
    # than the last statement) must not move the start forward.
    latest = session.exec(
        select(Transaction.paid_date)
        .join(Document, Transaction.document_id == Document.id)
        .where(Transaction.account_id == link.account_id, Transaction.paid_date.is_not(None),
               Transaction.external_id.is_(None), Document.category == "statement")  # type: ignore[attr-defined]
        .order_by(Transaction.paid_date.desc())  # type: ignore[attr-defined]
    ).first()
    if latest is None:
        latest = session.exec(
            select(Transaction.paid_date)
            .where(Transaction.account_id == link.account_id, Transaction.paid_date.is_not(None),
                   Transaction.external_id.is_(None))  # type: ignore[attr-defined]
            .order_by(Transaction.paid_date.desc())  # type: ignore[attr-defined]
        ).first()
    return (latest - timedelta(days=3)) if latest else today - timedelta(days=85)


def _has_reference(raw: dict) -> bool:
    return bool(raw.get("entry_reference") or raw.get("transaction_id"))


def _row_date(raw: dict) -> str:
    """The transaction's date. Banks that give no reference number (Santander's
    credit card, seen live 2026-10-05) stamp booking_date with the day we fetched
    and carry the real date in transaction_date, so for those rows prefer it."""
    if not _has_reference(raw) and raw.get("transaction_date"):
        return raw["transaction_date"]
    return raw.get("booking_date") or raw.get("value_date") or raw.get("transaction_date") or ""


def _external_id(raw: dict) -> str:
    ref = raw.get("entry_reference") or raw.get("transaction_id")
    if ref:
        return str(ref)
    amount = raw.get("transaction_amount") or {}
    balance = (raw.get("balance_after_transaction") or {}).get("amount")
    # Stable across fetch days: real transaction date + running balance, never the
    # fetch-day booking date.
    basis = "|".join([
        _row_date(raw), str(amount.get("amount")), str(amount.get("currency")),
        str(raw.get("credit_debit_indicator")), " ".join(raw.get("remittance_information") or []),
        str(balance),
    ])
    return hashlib.sha256(basis.encode()).hexdigest()


def _renumbered_twin(session: Session, account_id: int, external_id: str, kind: TransactionType,
                     amount: float, fetched: set) -> bool:
    """Santander ends its entry references with a 4-digit sequence ('...0001') and has been seen
    to renumber a booking ('...0001' -> '...0002') between two fetches (seen live 2026-10-07: three
    credits stored twice). A new reference that differs from a stored one only in that sequence, for
    the same amount and direction, where the stored reference is no longer in this fetch, is the
    same booking. Genuine twins come back together in one fetch, so they never match this."""
    if len(external_id) < 8 or not external_id.isdigit():
        return False
    twins = session.exec(select(Transaction).where(
        Transaction.account_id == account_id, Transaction.external_id.like(external_id[:-4] + "%"),  # type: ignore[union-attr]
        Transaction.transaction_type == kind, Transaction.amount == amount)).all()
    return any(t.external_id != external_id and t.external_id not in fetched for t in twins)


def _provider(raw: dict, is_credit: bool) -> str:
    party = (raw.get("debtor") if is_credit else raw.get("creditor")) or {}
    return party.get("name") or " ".join(raw.get("remittance_information") or []).strip() or "Unknown"


def _match_existing(session: Session, account_id: int, kind: TransactionType,
                    amount: float, paid: date, used_ids: set) -> Optional[int]:
    """One-to-one: each stored statement row can absorb at most one bank row."""
    lo, hi = paid - timedelta(days=_OVERLAP_DAYS), paid + timedelta(days=_OVERLAP_DAYS)
    candidates = session.exec(
        select(Transaction).where(
            Transaction.account_id == account_id, Transaction.external_id.is_(None),  # type: ignore[attr-defined]
            Transaction.transaction_type == kind, Transaction.paid_date >= lo, Transaction.paid_date <= hi,
        )
    ).all()
    for candidate in candidates:
        if candidate.id in used_ids:
            continue
        if round(candidate.amount, 2) == round(amount, 2):
            return candidate.id
    return None


def _account_label(session: Session, link: BankAccountLink, connection: Optional[BankConnection]) -> str:
    return msg.label(connection.bank_name if connection else "The bank", link.display_name)


def _fail(session: Session, link: BankAccountLink, result: SyncResult, problem: Problem, *,
          technical: Optional[str], dry_run: bool, store: bool = True, ref: Optional[str] = None) -> None:
    """One place that turns a failed sync into the person-facing message, the stored per-account error
    and the Problems log entry (cleared by the next successful sync of this account)."""
    result.problem = problem
    if store:
        result.error = problem.text
    if dry_run:
        return
    if store:
        fresh = session.get(BankAccountLink, link.id) or link
        fresh.last_error = problem.text
        session.add(fresh)
        session.commit()
    record_problem(session, area=msg.AREA, problem=problem, ref=ref or msg.link_ref(link.id), technical=technical)


def _failure_of(exc: Exception, account: str) -> Problem:
    if isinstance(exc, BankApiError):
        return msg.bank_rate_limited(account) if exc.rate_limited else msg.bank_not_answering(account)
    return msg.bank_not_answering(account, reached=False)


async def sync_link(session: Session, client, link: BankAccountLink, *, dry_run: bool = False,
                    scheduled: bool = True, today: Optional[date] = None,
                    classify=classify_transaction) -> SyncResult:
    result = SyncResult()
    now = datetime.utcnow()
    today = today or now.date()
    limit = SCHEDULED_CALL_LIMIT if scheduled else DAILY_CALL_LIMIT
    connection = session.get(BankConnection, link.connection_id)
    account = _account_label(session, link, connection)
    if _calls_last_24h(session, link.id, now) >= limit:
        result.skipped_quota = True
        # Not a failure of the account (nothing is stored on it), but never silent either.
        _fail(session, link, result, msg.daily_budget_used(account), technical=None, dry_run=dry_run, store=False)
        return result
    date_from = _window_start(session, link, today)

    session.add(BankApiCall(link_id=link.id, called_at=now, kind="transactions"))
    session.commit()  # the call is spent even if it fails
    try:
        rows = await client.list_transactions(link.bank_account_uid, date_from, today)
    except (BankApiError, httpx.HTTPError) as exc:
        if isinstance(exc, BankApiError) and (
                exc.status in (401, 403) or "SESSION" in exc.code.upper() or "CONSENT" in exc.code.upper()):
            connection.status = BankConnectionStatus.EXPIRED
            session.add(connection)
            session.commit()
            problem = msg.consent_expired(connection.bank_name)
            # The access is what ended, so the Problems entry belongs to the connection, not the account.
            _fail(session, link, result, problem, technical=str(exc), dry_run=dry_run,
                  ref=msg.connection_ref(connection.id))
            return result
        _fail(session, link, result, _failure_of(exc, account), dry_run=dry_run,
              technical=str(exc) if isinstance(exc, BankApiError) else repr(exc))
        return result

    result.fetched = len(rows)
    document = sync_document(session, link.account_id, connection.bank_name)
    if not dry_run:
        session.commit()  # the rolling document must survive any later rollback
    loop_finished = True
    fetched_refs = {_external_id(r) for r in rows}
    for raw in rows:
        try:
            external_id = _external_id(raw)
            exists = session.exec(select(Transaction).where(
                Transaction.account_id == link.account_id, Transaction.external_id == external_id)).first()
            if exists is not None:
                result.already_synced += 1
                continue
            amount_info = raw.get("transaction_amount") or {}
            is_credit = raw.get("credit_debit_indicator") == "CRDT"
            kind = TransactionType.CREDIT if is_credit else TransactionType.DEBIT
            paid = date.fromisoformat(_row_date(raw))
            amount = float(abs(Decimal(str(amount_info.get("amount")))))
        except (KeyError, ValueError, InvalidOperation, TypeError):
            logger.warning("bank sync: skipping unparsable row for link %s: %r", link.id, raw)
            result.skipped_rows += 1
            continue
        if _renumbered_twin(session, link.account_id, external_id, kind, amount, fetched_refs):
            result.already_synced += 1
            continue
        match_id = _match_existing(session, link.account_id, kind, amount, paid, result._used_match_ids)
        if match_id is not None:
            result._used_match_ids.add(match_id)
            result.matched_existing += 1
            continue
        result.created += 1
        if dry_run:
            continue
        try:
            txn = Transaction(
                document_id=document.id, description=_provider(raw, is_credit), category=Category.OTHER,
                transaction_type=kind, amount=amount, currency=amount_info.get("currency", "EUR"),
                paid_date=paid, statement_period=paid.strftime("%Y-%m"),
                account_id=link.account_id, external_id=external_id,
            )
            # No add/flush before classify: classify may await the LLM, and a
            # flushed INSERT would hold SQLite's write lock for the whole call
            # (blocking the family's own writes and any parallel sync).
            try:
                await classify(session, txn)
                session.add(txn)
                # classify already filed the transaction under the merchant's tree
                # node (dual-writing the legacy category). Only a merchant with no
                # node yet (legacy row) still hands over its legacy category.
                if txn.merchant_id is not None:
                    merchant = session.get(Merchant, txn.merchant_id)
                    if (merchant is not None and merchant.default_category_id is None
                            and merchant.default_category != Category.OTHER):
                        txn.category = merchant.default_category
            except Exception:
                session.rollback()
                # Keep the transaction: the bank reference prevents duplicates on
                # a re-run, so re-fetch it and store it plainly.
                document = sync_document(session, link.account_id, connection.bank_name)
                txn = session.exec(select(Transaction).where(
                    Transaction.account_id == link.account_id, Transaction.external_id == external_id)).first()
                if txn is None:
                    txn = Transaction(
                        document_id=document.id, description=_provider(raw, is_credit), category=Category.OTHER,
                        transaction_type=kind, amount=amount, currency=amount_info.get("currency", "EUR"),
                        paid_date=paid, statement_period=paid.strftime("%Y-%m"),
                        account_id=link.account_id, external_id=external_id,
                    )
                    session.add(txn)
                    session.flush()
                txn.category = Category.OTHER
                txn.category_id = None  # unfiled: surfaces in Needs Review
                txn.merchant_id = None
                session.add(txn)
                result.unclassified += 1
            session.commit()  # commit after EACH transaction — never hold the DB open across classify calls
        except Exception:
            # Any unexpected failure on one row must not crash the whole job
            # (three accounts, three times a day, unattended).
            session.rollback()
            logger.exception("bank sync: unexpected failure on a row for link %s", link.id)
            save_problem, save_technical = msg.save_failed(account), traceback.format_exc()
            result.problem, result.error = save_problem, save_problem.text
            loop_finished = False
            break
    if not dry_run and loop_finished:
        link.last_synced_at = now
        link.last_error = None
        session.add(link)
        session.commit()
        # It worked, so whatever was wrong with this account (or its bank access) is over.
        resolve_for(session, msg.link_ref(link.id))
        resolve_for(session, msg.connection_ref(connection.id))
        reconcile_quietly(session)  # bills filed before these debits arrived are now settled by them
    elif not loop_finished:
        _fail(session, link, result, save_problem, technical=save_technical, dry_run=dry_run)
    elif dry_run:
        session.rollback()
    return result
