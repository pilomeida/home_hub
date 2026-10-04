# tests/test_bank_router.py
from datetime import datetime, timedelta, timezone

import pytest

from app.models.bank import BankAccountLink, BankConnection, BankConnectionStatus


class FakeClient:
    def __init__(self, rows=None):
        self.rows = rows or []

    async def start_auth(self, **kwargs):
        from app.services.bankapi.client import AuthStart
        return AuthStart("https://bank/x", "a1")

    async def create_session(self, code):
        from app.services.bankapi.client import BankSession, BankSessionAccount
        return BankSession(
            "sess",
            [BankSessionAccount("u1", "PT50123", "Conta", "EUR")],
            datetime(2027, 3, 1, tzinfo=timezone.utc),
        )

    async def max_consent_days(self, bank_name, country):
        return 180


@pytest.fixture()
def bank_client(monkeypatch):
    from app.main import app
    from app.routers.bank import get_bank_client
    monkeypatch.setattr("app.services.storage.settings.ENABLE_BANKING_APP_ID", "app-123")
    monkeypatch.setattr("app.services.storage.settings.ENABLE_BANKING_KEY_PATH", "key.pem")
    app.dependency_overrides[get_bank_client] = lambda: FakeClient()
    yield
    app.dependency_overrides.pop(get_bank_client, None)


def _seed(session, status=BankConnectionStatus.ACTIVE):
    conn = BankConnection(bank_name="Santander Totta", country="PT", state="st1", status=status)
    session.add(conn); session.commit(); session.refresh(conn)
    link = BankAccountLink(connection_id=conn.id, bank_account_uid="u1", iban="PT50123",
                           display_name="Conta")
    session.add(link); session.commit(); session.refresh(link)
    return conn, link


def test_connections_page_lists_seeded_connection(client, session, bank_client):
    _seed(session)
    r = client.get("/financials/bank/")
    assert r.status_code == 200
    assert "Santander" in r.text


def test_connections_page_works_without_bank_key(client, session):
    _seed(session)
    r = client.get("/financials/bank/")
    assert r.status_code == 200
    assert "Santander" in r.text


def test_callback_with_unknown_state_is_friendly_400(client, session, bank_client):
    r = client.get("/financials/bank/callback", params={"state": "bad", "code": "x"},
                   follow_redirects=False)
    assert r.status_code == 400
    assert "expired or was already used" in r.text


def test_map_post_saves_account_id(client, session, bank_client):
    conn, link = _seed(session)
    from app.models.account import Account
    acct = Account(name="Conta", institution="Santander"); session.add(acct); session.commit()
    r = client.post(f"/financials/bank/{conn.id}/map/{link.id}", data={"account_id": str(acct.id)},
                    follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()  # the route wrote through its own Session
    assert session.get(BankAccountLink, link.id).account_id == acct.id


def test_map_post_empty_choice_unmaps(client, session, bank_client):
    conn, link = _seed(session)
    from app.models.account import Account
    acct = Account(name="Conta", institution="Santander"); session.add(acct); session.commit()
    link.account_id = acct.id; session.commit()
    r = client.post(f"/financials/bank/{conn.id}/map/{link.id}", data={"account_id": ""},
                    follow_redirects=False)
    assert r.status_code == 303
    session.expire_all()
    assert session.get(BankAccountLink, link.id).account_id is None


def test_map_post_unknown_account_is_friendly_400(client, session, bank_client):
    conn, link = _seed(session)
    r = client.post(f"/financials/bank/{conn.id}/map/{link.id}", data={"account_id": "9999"})
    assert r.status_code == 400
    assert "Pick one of the listed Hub accounts" in r.text


def test_map_post_link_of_other_connection_is_404(client, session, bank_client):
    other = BankConnection(bank_name="Revolut", country="LT", state="st2",
                           status=BankConnectionStatus.ACTIVE)
    session.add(other); session.commit(); session.refresh(other)
    other_link = BankAccountLink(connection_id=other.id, bank_account_uid="u2")
    session.add(other_link); session.commit(); session.refresh(other_link)
    conn, _ = _seed(session)
    r = client.post(f"/financials/bank/{conn.id}/map/{other_link.id}", data={"account_id": "1"})
    assert r.status_code == 404


# --- Task 6: Sync now, banners, Checkpoint-3 carry-overs ---

def _sync_now_seed(session):
    from app.models.account import Account
    conn = BankConnection(bank_name="Santander Totta", country="PT", state="st-sync",
                          status=BankConnectionStatus.ACTIVE)
    session.add(conn); session.commit(); session.refresh(conn)
    acct = Account(name="Conta Santander", institution="Santander")
    session.add(acct); session.commit(); session.refresh(acct)
    link = BankAccountLink(connection_id=conn.id, bank_account_uid="u1", account_id=acct.id)
    session.add(link); session.commit(); session.refresh(link)
    return conn, link


def test_sync_now_creates_transactions_and_shows_summary(client, session, bank_client):
    conn, link = _sync_now_seed(session)

    class SyncClient(FakeClient):
        async def list_transactions(self, account_uid, date_from, date_to):
            return [
                {"entry_reference": "r1", "status": "BOOK", "credit_debit_indicator": "DBTR",
                 "transaction_amount": {"amount": "10.00", "currency": "EUR"},
                 "booking_date": "2026-09-30", "creditor": {"name": "Cafe"}},
                {"entry_reference": "r2", "status": "BOOK", "credit_debit_indicator": "DBTR",
                 "transaction_amount": {"amount": "20.00", "currency": "EUR"},
                 "booking_date": "2026-09-30", "creditor": {"name": "Shop"}}]

    from app.main import app
    from app.routers.bank import get_bank_client
    app.dependency_overrides[get_bank_client] = lambda: SyncClient()
    r = client.post(f"/financials/bank/{conn.id}/sync", follow_redirects=True)
    assert r.status_code == 200
    assert "2 new" in r.text and "bank" in r.text.lower()
    session.expire_all()
    from app.models.transaction import Transaction
    assert session.query(Transaction).count() == 2
    app.dependency_overrides.pop(get_bank_client, None)


def test_bank_notices_renewal_warning_at_14_days(session):
    from app.routers.bank import bank_notices
    conn, link = _sync_now_seed(session)
    now = datetime(2026, 10, 4, 12, 0)
    conn.valid_until = now + timedelta(days=14)
    session.add(conn); session.commit()
    notices = bank_notices(session, now=now)
    assert any("Santander Totta access ends in 14 days" in n for n in notices)


def test_bank_notices_silent_at_15_days(session):
    from app.routers.bank import bank_notices
    conn, link = _sync_now_seed(session)
    now = datetime(2026, 10, 4, 12, 0)
    conn.valid_until = now + timedelta(days=15)
    session.add(conn); session.commit()
    assert bank_notices(session, now=now) == []


def test_bank_notices_expired(session):
    from app.routers.bank import bank_notices
    conn, link = _sync_now_seed(session)
    conn.status = BankConnectionStatus.EXPIRED
    session.add(conn); session.commit()
    notices = bank_notices(session)
    assert any("Santander Totta access has ended" in n for n in notices)


def test_bank_notices_stale_last_synced(session):
    from app.routers.bank import bank_notices
    conn, link = _sync_now_seed(session)
    link.last_synced_at = datetime.utcnow() - timedelta(days=3)
    session.add(link); session.commit()
    notices = bank_notices(session)
    assert any("hasn't updated since" in n for n in notices)


def test_bank_notices_on_error(session):
    from app.routers.bank import bank_notices
    conn, link = _sync_now_seed(session)
    link.last_error = "The bank asked us to wait; will retry later"
    session.add(link); session.commit()
    notices = bank_notices(session)
    assert any("hasn't updated" in n for n in notices)


def test_banner_shows_on_financials_pages_not_others(client, session):
    from app.models.document import Document, DocumentSource
    from app.models.domain import Domain
    conn, link = _sync_now_seed(session)
    conn.valid_until = datetime.utcnow() + timedelta(days=5, hours=2)
    session.add(conn); session.commit()
    doc = Document(filename="s.pdf", file_path="/x", content_hash="b1",
                   source=DocumentSource.API, domain=Domain.FINANCIALS)
    session.add(doc); session.commit()
    assert "ends in 5 days" in client.get("/financials/bank/").text
    assert "ends in 5 days" in client.get("/financials/bills").text
    assert "ends in 5 days" not in client.get("/wiki").text


def test_pending_connection_shows_try_again(client, session):
    from app.models.bank import BankConnection
    conn, link = _seed(session, status=BankConnectionStatus.PENDING)
    r = client.get("/financials/bank/")
    assert r.status_code == 200
    assert "Try again" in r.text


def test_callback_valueerror_renders_friendly_page(client, session, bank_client):
    r = client.get("/financials/bank/callback", params={"state": "bad", "code": "x"})
    assert r.status_code == 400
    assert "expired or was already used" in r.text
    assert "Back to bank connections" in r.text


def test_map_post_unknown_account_id_junk_is_friendly_400(client, session, bank_client):
    conn, link = _seed(session)
    r = client.post(f"/financials/bank/{conn.id}/map/{link.id}", data={"account_id": "junk"})
    assert r.status_code == 400
    assert "Pick one of the listed Hub accounts" in r.text


def test_map_post_redirects_to_connections_not_map(client, session, bank_client):
    conn, link = _seed(session)
    from app.models.account import Account
    acct = Account(name="Conta", institution="Santander"); session.add(acct); session.commit()
    r = client.post(f"/financials/bank/{conn.id}/map/{link.id}", data={"account_id": str(acct.id)},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/financials/bank/"


# --- Round 2: renewal carries mapping, supersedes old connections ---

def test_renewal_carries_mapping_and_supersedes_old_connection(session, bank_client, client):
    from datetime import timedelta
    from app.models.account import Account
    from app.models.bank import BankConnection, BankConnectionStatus
    from app.services.bankapi.connect import complete_connection
    from app.jobs.bank_sync import run_all

    # First connection, authorized, mapped, synced at T.
    first = BankConnection(bank_name="Santander Totta", country="PT", state="old-state",
                           status=BankConnectionStatus.ACTIVE)
    session.add(first); session.commit(); session.refresh(first)
    acct = Account(name="Conta Santander", institution="Santander", identifier="PT50123")
    session.add(acct); session.commit(); session.refresh(acct)
    old_link = BankAccountLink(connection_id=first.id, bank_account_uid="old-uid",
                               iban="PT50123", display_name="Conta", account_id=acct.id)
    T = datetime.utcnow() - timedelta(hours=5)
    old_link.last_synced_at = T
    session.add(old_link); session.commit(); session.refresh(old_link)

    # Renewal: begin + complete a second authorization for the same bank/IBAN.
    conn = BankConnection(bank_name="Santander Totta", country="PT", state="st1",
                          status=BankConnectionStatus.PENDING)
    session.add(conn); session.commit(); session.refresh(conn)
    renewed = _bank_connect_complete(session, FakeClient(), state=conn.state, code="code-1")
    new_link = session.query(BankAccountLink).filter(
        BankAccountLink.connection_id == renewed.id).one()
    assert new_link.account_id == acct.id
    assert new_link.last_synced_at == T
    session.expire_all()
    assert session.get(BankConnection, first.id).status == BankConnectionStatus.EXPIRED

    # The job syncs exactly one link now.
    results = _run(run_all(session, FakeClient([]), scheduled=True))
    assert set(results) == {new_link.id}

    # No false banners from the dead connection.
    from app.routers.bank import bank_notices
    assert bank_notices(session) == []


def test_sync_now_daily_limit_message(client, session, bank_client):
    conn, link = _sync_now_seed(session)
    from app.models.bank import BankApiCall
    now = datetime.utcnow()
    for i in range(4):
        session.add(BankApiCall(link_id=link.id, called_at=now - timedelta(hours=i),
                                kind="transactions"))
    session.commit()
    r = client.post(f"/financials/bank/{conn.id}/sync", follow_redirects=True)
    assert r.status_code == 200
    assert "Daily limit reached for this bank — try again tomorrow." in r.text


def _bank_connect_complete(session, client, *, state, code):
    from app.services.bankapi.connect import complete_connection
    import asyncio
    return asyncio.run(complete_connection(session, client, state=state, code=code))


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_lisbon_datetime_converts_utc_summer_and_winter():
    from datetime import datetime

    from app.services.presentation import lisbon_datetime

    assert lisbon_datetime(datetime(2026, 7, 1, 12, 0)) == "01 Jul 2026, 13:00"  # WEST = UTC+1
    assert lisbon_datetime(datetime(2026, 12, 1, 12, 0)) == "01 Dec 2026, 12:00"  # WET = UTC
    assert lisbon_datetime(None) == "—"
