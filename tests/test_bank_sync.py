# tests/test_bank_sync.py
from datetime import date, datetime, timedelta

import pytest

from app.models.account import Account
from app.models.bank import BankApiCall, BankAccountLink, BankConnection, BankConnectionStatus
from app.models.document import Document, DocumentSource
from app.models.domain import Domain
from app.models.transaction import Transaction, TransactionType
from app.services.bankapi.sync import DAILY_CALL_LIMIT, SCHEDULED_CALL_LIMIT, sync_link


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def list_transactions(self, account_uid, date_from, date_to):
        self.calls += 1
        return self.rows


async def fake_classify(session, transaction, client=None):
    transaction.merchant_id = None
    return None


def make_link(session):
    conn = BankConnection(bank_name="Santander Totta", country="PT", state="st-sync",
                          status=BankConnectionStatus.ACTIVE)
    session.add(conn); session.commit(); session.refresh(conn)
    acct = Account(name="Conta Santander", institution="Santander")
    session.add(acct); session.commit(); session.refresh(acct)
    link = BankAccountLink(connection_id=conn.id, bank_account_uid="u1", account_id=acct.id)
    session.add(link); session.commit(); session.refresh(link)
    return link, acct


def test_quota_skips_and_spends_on_real_fetch(session):
    link, _ = make_link(session)
    now = datetime.utcnow()
    for i in range(DAILY_CALL_LIMIT):
        session.add(BankApiCall(link_id=link.id, called_at=now - timedelta(hours=i), kind="transactions"))
    session.commit()
    result = _run(sync_link(session, FakeClient([]), link, today=date(2026, 10, 4)))
    assert result.skipped_quota is True
    assert result.created == 0

    # 3 calls in the window still leaves room (the bank cap of 4 is the limit)
    session.query(BankApiCall).delete()
    for label, hours in (("now-24h+5s", 23.998), ("now-18h", 18), ("now-11h", 11)):
        session.add(BankApiCall(link_id=link.id, called_at=now - timedelta(hours=hours), kind="transactions"))
    session.commit()
    result = _run(sync_link(session, FakeClient([]), link, today=date(2026, 10, 4)))
    assert result.skipped_quota is False


def test_first_sync_window_looks_back_from_latest_transaction(session):
    link, account = make_link(session)
    doc = _doc(session)
    session.add(Transaction(document_id=doc.id, provider="X", amount=5.0,
                            account_id=account.id, paid_date=date(2026, 9, 20)))
    session.commit()
    seen = {}

    class RecordingClient(FakeClient):
        async def list_transactions(self, account_uid, date_from, date_to):
            seen["from"], seen["to"] = date_from, date_to
            return []

    _run(sync_link(session, RecordingClient([]), link, today=date(2026, 10, 4)))
    assert seen["from"] == date(2026, 9, 17) and seen["to"] == date(2026, 10, 4)


def test_maps_booked_rows_into_transactions(session):
    link, account = make_link(session)
    rows = [{
        "entry_reference": "r1", "status": "BOOK", "credit_debit_indicator": "DBTR",
        "transaction_amount": {"amount": "23.40", "currency": "EUR"},
        "booking_date": "2026-09-30", "creditor": {"name": "Continente"},
        "remittance_information": ["compras"],
    }]
    result = _run(sync_link(session, FakeClient(rows), link, today=date(2026, 10, 4), classify=fake_classify))
    assert result.created == 1
    txn = session.query(Transaction).one()
    assert txn.external_id == "r1" and txn.amount == 23.40
    assert txn.transaction_type == TransactionType.DEBIT
    assert txn.provider == "Continente" and txn.paid_date == date(2026, 9, 30)
    assert txn.statement_period == "2026-09"


def test_falls_back_to_hash_when_no_reference(session):
    link, account = make_link(session)
    rows = [{"status": "BOOK", "credit_debit_indicator": "CRDT",
             "transaction_amount": {"amount": "10.00", "currency": "EUR"},
             "booking_date": "2026-09-30", "debtor": {"name": "Patrao"}}]
    _run(sync_link(session, FakeClient(rows), link, today=date(2026, 10, 4), classify=fake_classify))
    txn = session.query(Transaction).one()
    assert txn.external_id and len(txn.external_id) == 64
    assert txn.transaction_type == TransactionType.CREDIT and txn.provider == "Patrao"


@pytest.mark.asyncio
async def test_statement_overlap_is_not_double_counted(session):
    link, account = make_link(session)
    statement_doc = Document(filename="s.pdf", file_path="/x", content_hash="s1",
                             source=DocumentSource.MANUAL, category="statement")
    session.add(statement_doc); session.commit(); session.refresh(statement_doc)
    session.add(Transaction(document_id=statement_doc.id, provider="CONTINENTE", amount=23.40,
                            account_id=account.id, transaction_type=TransactionType.DEBIT,
                            paid_date=date(2026, 9, 29)))
    session.commit()
    rows = [{"entry_reference": "r1", "status": "BOOK", "credit_debit_indicator": "DBTR",
             "transaction_amount": {"amount": "23.40", "currency": "EUR"},
             "booking_date": "2026-09-30", "creditor": {"name": "Continente"}}]
    result = await sync_link(session, FakeClient(rows), link, today=date(2026, 10, 4), classify=fake_classify)
    assert (result.created, result.matched_existing) == (0, 1)


def test_same_reference_twice_is_already_synced(session):
    link, account = make_link(session)
    rows = [{"entry_reference": "r1", "status": "BOOK", "credit_debit_indicator": "DBTR",
             "transaction_amount": {"amount": "23.40", "currency": "EUR"},
             "booking_date": "2026-09-30", "creditor": {"name": "Continente"}}]
    _run(sync_link(session, FakeClient(rows), link, today=date(2026, 10, 4), classify=fake_classify))
    result = _run(sync_link(session, FakeClient(rows), link, today=date(2026, 10, 4), classify=fake_classify))
    assert result.already_synced == 1 and result.created == 0


def test_dry_run_writes_nothing_but_records_the_call(session):
    link, account = make_link(session)
    rows = [{"entry_reference": "r1", "status": "BOOK", "credit_debit_indicator": "DBTR",
             "transaction_amount": {"amount": "23.40", "currency": "EUR"},
             "booking_date": "2026-09-30", "creditor": {"name": "Continente"}}]
    result = _run(sync_link(session, FakeClient(rows), link, dry_run=True,
                            today=date(2026, 10, 4), classify=fake_classify))
    assert result.created == 1
    assert session.query(Transaction).count() == 0
    assert session.query(BankApiCall).count() == 1
    assert link.last_synced_at is None  # dry run must not advance the window


def test_rate_limit_sets_friendly_error_and_keeps_window(session):
    link, _ = make_link(session)

    class RateLimitedClient:
        async def list_transactions(self, account_uid, date_from, date_to):
            from app.services.bankapi.client import BankApiError
            raise BankApiError(429, "ASPSP_RATE_LIMIT_EXCEEDED", "slow down")

    result = _run(sync_link(session, RateLimitedClient(), link, today=date(2026, 10, 4)))
    assert result.error == "The bank asked us to wait; will retry later"
    assert link.last_error == result.error
    assert link.last_synced_at is None


def test_session_error_expires_the_connection(session):
    link, _ = make_link(session)

    class DeadSessionClient:
        async def list_transactions(self, account_uid, date_from, date_to):
            from app.services.bankapi.client import BankApiError
            raise BankApiError(401, "SESSION_NOT_FOUND", "gone")

    result = _run(sync_link(session, DeadSessionClient(), link, today=date(2026, 10, 4)))
    assert result.error == "Bank access has ended; renew it"
    assert session.get(BankConnection, link.connection_id).status == BankConnectionStatus.EXPIRED


def test_row_without_date_is_skipped_and_counted(session):
    link, account = make_link(session)
    good = [{"entry_reference": "r1", "status": "BOOK", "credit_debit_indicator": "DBTR",
             "transaction_amount": {"amount": "23.40", "currency": "EUR"},
             "booking_date": "2026-09-30", "creditor": {"name": "Continente"}}]
    bad = {"status": "BOOK", "credit_debit_indicator": "DBTR",
           "transaction_amount": {"amount": "5.00", "currency": "EUR"},
           "creditor": {"name": "Mystery"}}  # no date at all
    bad_amount = {"entry_reference": "r2", "status": "BOOK", "credit_debit_indicator": "DBTR",
                  "transaction_amount": {"amount": "not-a-number", "currency": "EUR"},
                  "booking_date": "2026-09-30", "creditor": {"name": "Odd"}}
    result = _run(sync_link(session, FakeClient(good + [bad, bad_amount]), link,
                            today=date(2026, 10, 4), classify=fake_classify))
    assert result.skipped_rows == 2
    assert result.created == 1
    assert session.query(Transaction).count() == 1


def test_classify_failure_keeps_the_transaction_as_other(session):
    link, account = make_link(session)
    rows = [{"entry_reference": "r1", "status": "BOOK", "credit_debit_indicator": "DBTR",
             "transaction_amount": {"amount": "23.40", "currency": "EUR"},
             "booking_date": "2026-09-30", "creditor": {"name": "Continente"}}]

    async def failing_classify(session, transaction, client=None):
        raise RuntimeError("gateway down")

    result = _run(sync_link(session, FakeClient(rows), link, today=date(2026, 10, 4),
                            classify=failing_classify))
    assert result.created == 1 and result.unclassified == 1
    txn = session.query(Transaction).one()
    assert txn.provider == "Continente"
    assert txn.merchant_id is None
    from app.models.transaction import Category
    assert txn.category == Category.OTHER


def test_classify_failure_on_first_row_does_not_break_the_batch(session):
    link, account = make_link(session)
    rows = [
        {"entry_reference": "r1", "status": "BOOK", "credit_debit_indicator": "DBTR",
         "transaction_amount": {"amount": "10.00", "currency": "EUR"},
         "booking_date": "2026-09-29", "creditor": {"name": "First"}},
        {"entry_reference": "r2", "status": "BOOK", "credit_debit_indicator": "DBTR",
         "transaction_amount": {"amount": "20.00", "currency": "EUR"},
         "booking_date": "2026-09-30", "creditor": {"name": "Second"}},
    ]
    calls = {"n": 0}

    async def flaky_classify(session, transaction, client=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("gateway down")
        transaction.merchant_id = None

    result = _run(sync_link(session, FakeClient(rows), link, today=date(2026, 10, 4),
                            classify=flaky_classify))
    assert result.created == 2 and result.unclassified == 1
    txns = session.query(Transaction).all()
    assert len(txns) == 2
    assert {t.document_id for t in txns} and all(t.document_id for t in txns)


def test_unexpected_row_error_stops_cleanly(session, monkeypatch):
    link, _ = make_link(session)
    rows = [
        {"entry_reference": "r1", "status": "BOOK", "credit_debit_indicator": "DBTR",
         "transaction_amount": {"amount": "10.00", "currency": "EUR"},
         "booking_date": "2026-09-29", "creditor": {"name": "Fine"}},
        {"entry_reference": "r2", "status": "BOOK", "credit_debit_indicator": "DBTR",
         "transaction_amount": {"amount": "20.00", "currency": "EUR"},
         "booking_date": "2026-09-30", "creditor": {"name": "Boom"}},
    ]
    real_provider = None
    import app.services.bankapi.sync as sync_mod
    real_provider = sync_mod._provider

    def exploding_provider(raw, is_credit):
        if raw.get("entry_reference") == "r2":
            raise RuntimeError("boom")
        return real_provider(raw, is_credit)

    monkeypatch.setattr(sync_mod, "_provider", exploding_provider)
    result = _run(sync_link(session, FakeClient(rows), link, today=date(2026, 10, 4),
                            classify=fake_classify))
    assert result.error == "Something went wrong saving the bank transactions; some may be missing"
    txns = session.query(Transaction).all()
    assert len(txns) == 1 and txns[0].external_id == "r1"  # row 1 kept
    assert link.last_synced_at is None  # window not advanced
    session.expire_all()
    assert session.get(BankAccountLink, link.id).last_error == result.error


def test_identical_coffees_match_one_to_one(session):
    link, account = make_link(session)
    statement_doc = Document(filename="s.pdf", file_path="/x", content_hash="s2",
                             source=DocumentSource.MANUAL, category="statement")
    session.add(statement_doc); session.commit(); session.refresh(statement_doc)
    session.add(Transaction(document_id=statement_doc.id, provider="CAFE", amount=1.50,
                            account_id=account.id, transaction_type=TransactionType.DEBIT,
                            paid_date=date(2026, 9, 29)))
    session.commit()
    coffee = {"credit_debit_indicator": "DBTR",
              "transaction_amount": {"amount": "1.50", "currency": "EUR"}}
    rows = [
        {**coffee, "entry_reference": "c1", "status": "BOOK", "booking_date": "2026-09-29",
         "creditor": {"name": "Cafe A"}},
        {**coffee, "entry_reference": "c2", "status": "BOOK", "booking_date": "2026-09-30",
         "creditor": {"name": "Cafe B"}},
    ]
    result = _run(sync_link(session, FakeClient(rows), link, today=date(2026, 10, 4), classify=fake_classify))
    assert (result.created, result.matched_existing) == (1, 1)


def _doc(session):
    d = Document(filename="sync", file_path="", content_hash="bank-sync:seed",
                 source=DocumentSource.API, domain=Domain.FINANCIALS)
    session.add(d); session.commit(); session.refresh(d)
    return d


def _run(coro):
    import asyncio
    return asyncio.run(coro)


@pytest.mark.asyncio
async def test_sync_does_not_hold_the_database_write_lock_while_classifying(session, engine):
    """Live bug 2026-10-04: a flushed INSERT before the (slow, LLM) classify call
    held SQLite's write lock, so a parallel sync / family write failed with
    'database is locked'. During classify another connection must be able to write."""
    import sqlite3

    link, acct = make_link(session)
    db_path = engine.url.database
    seen = {}

    async def classify_that_probes(sess, transaction, client=None):
        other = sqlite3.connect(db_path, timeout=0.2)
        try:
            other.execute("insert into accounts(name, institution, currency, account_type, created_at) "
                          "values ('probe','x','EUR','CHECKING','2026-01-01')")
            other.commit()
            seen["wrote"] = True
        except sqlite3.OperationalError as exc:
            seen["error"] = str(exc)
        finally:
            other.close()
        transaction.merchant_id = None

    rows = [{"entry_reference": "r1", "status": "BOOK", "credit_debit_indicator": "DBTR",
             "transaction_amount": {"amount": "5.00", "currency": "EUR"},
             "booking_date": "2026-10-01", "creditor": {"name": "Cafe"}}]
    result = await sync_link(session, FakeClient(rows), link, today=date(2026, 10, 4),
                             classify=classify_that_probes)
    assert seen == {"wrote": True}, seen
    assert result.created == 1


def test_first_sync_window_ignores_rows_the_sync_itself_wrote(session):
    """Live bug 2026-10-04: API-written rows (external_id set) dated Oct 4 moved the
    backfill start to Oct 1, hiding six weeks of real history."""
    link, account = make_link(session)
    doc = _doc(session)
    session.add(Transaction(document_id=doc.id, provider="stmt", amount=5.0,
                            account_id=account.id, paid_date=date(2026, 8, 14)))
    session.add(Transaction(document_id=doc.id, provider="api", amount=6.0, account_id=account.id,
                            paid_date=date(2026, 10, 4), external_id="from-api"))
    session.commit()
    seen = {}

    class RecordingClient(FakeClient):
        async def list_transactions(self, account_uid, date_from, date_to):
            seen["from"] = date_from
            return []

    _run(sync_link(session, RecordingClient([]), link, today=date(2026, 10, 5)))
    assert seen["from"] == date(2026, 8, 11)
