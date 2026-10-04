# tests/test_bank_job.py
from datetime import date, datetime, timedelta

import pytest

from app.models.account import Account
from app.models.bank import BankAccountLink, BankConnection, BankConnectionStatus
from app.models.transaction import Transaction, TransactionType
from app.jobs.bank_sync import run_all


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def list_transactions(self, account_uid, date_from, date_to):
        self.calls.append(account_uid)
        return self.rows


async def fake_classify(session, transaction, client=None):
    transaction.merchant_id = None
    return None


def _active_connection(session, bank_name="Santander Totta", state="st"):
    conn = BankConnection(bank_name=bank_name, country="PT", state=state,
                          status=BankConnectionStatus.ACTIVE)
    session.add(conn); session.commit(); session.refresh(conn)
    return conn


def test_only_active_mapped_links_are_synced(session):
    conn = _active_connection(session)
    acct = Account(name="Conta", institution="Santander"); session.add(acct); session.commit()
    mapped = BankAccountLink(connection_id=conn.id, bank_account_uid="u1", account_id=acct.id)
    unmapped = BankAccountLink(connection_id=conn.id, bank_account_uid="u2", account_id=None)
    session.add(mapped); session.add(unmapped); session.commit()
    expired_conn = _active_connection(session, state="st2")
    expired_conn.status = BankConnectionStatus.EXPIRED
    session.add(expired_conn); session.commit()
    expired_link = BankAccountLink(connection_id=expired_conn.id, bank_account_uid="u3",
                                   account_id=acct.id)
    session.add(expired_link); session.commit()

    seen = {}

    class RecordingClient(FakeClient):
        async def list_transactions(self, account_uid, date_from, date_to):
            seen["uid"] = account_uid
            return []

    results = _run(run_all(session, RecordingClient([]), scheduled=True))
    assert seen["uid"] == "u1" and len(seen) == 1  # unmapped + expired skipped
    assert set(results) == {mapped.id}


def test_dry_run_writes_no_transactions(session):
    conn = _active_connection(session)
    acct = Account(name="Conta", institution="Santander"); session.add(acct); session.commit()
    link = BankAccountLink(connection_id=conn.id, bank_account_uid="u1", account_id=acct.id)
    session.add(link); session.commit()
    rows = [{"entry_reference": "r1", "status": "BOOK", "credit_debit_indicator": "DBTR",
             "transaction_amount": {"amount": "10.00", "currency": "EUR"},
             "booking_date": "2026-09-30", "creditor": {"name": "X"}}]

    results = _run(run_all(session, FakeClient(rows), scheduled=True, dry_run=True))
    assert results[link.id].created == 1
    assert session.query(Transaction).count() == 0


def test_main_exits_zero_when_not_configured(monkeypatch, tmp_path):
    from app.config import settings
    import app.jobs.bank_sync as job
    monkeypatch.setattr(settings, "ENABLE_BANKING_APP_ID", "")
    monkeypatch.setattr(settings, "ENABLE_BANKING_KEY_PATH", "")
    import sys
    monkeypatch.setattr(sys, "argv", ["bank_sync"])
    assert job.main() == 0


def test_one_broken_link_does_not_stop_the_others(session, monkeypatch):
    conn = _active_connection(session)
    acct = Account(name="Conta", institution="Santander"); session.add(acct); session.commit()
    first = BankAccountLink(connection_id=conn.id, bank_account_uid="u1", account_id=acct.id)
    second = BankAccountLink(connection_id=conn.id, bank_account_uid="u2", account_id=acct.id)
    session.add(first); session.add(second); session.commit()

    import app.jobs.bank_sync as job
    from app.services.bankapi.sync import SyncResult

    calls = {"n": 0}

    async def flaky_sync_link(session, client, link, **kwargs):
        calls["n"] += 1
        if link.id == first.id:
            raise RuntimeError("boom")
        return SyncResult(created=1)

    monkeypatch.setattr(job, "sync_link", flaky_sync_link)
    results = _run(run_all(session, FakeClient([]), scheduled=True))
    assert results[first.id].error == "Unexpected failure"
    assert results[second.id].created == 1  # the other account still got its turn


def _run(coro):
    import asyncio
    return asyncio.run(coro)
