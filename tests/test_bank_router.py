# tests/test_bank_router.py
from datetime import datetime, timedelta, timezone

import pytest

from app.models.bank import BankAccountLink, BankConnection, BankConnectionStatus


class FakeClient:
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
    conn = BankConnection(bank_name="Santander", country="PT", state="st1", status=status)
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
