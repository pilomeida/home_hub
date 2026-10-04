# tests/test_bank_connect.py
from datetime import datetime, timezone

import pytest

from app.models.account import Account
from app.models.bank import BankAccountLink, BankConnection, BankConnectionStatus
from app.services.bankapi.client import AuthStart, BankSession, BankSessionAccount
from app.services.bankapi.connect import (
    begin_connection, complete_connection, connection_days_left,
)


class FakeClient:
    def __init__(self):
        self.start_auth_kwargs = None

    async def start_auth(self, **kwargs):
        self.start_auth_kwargs = kwargs
        return AuthStart("https://bank/x", "a1")

    async def create_session(self, code):
        return BankSession(
            "sess",
            [BankSessionAccount("u1", "PT50123", "Conta", "EUR")],
            datetime(2027, 3, 1, tzinfo=timezone.utc),
        )

    async def max_consent_days(self, bank_name, country):
        return 180


def test_begin_connection_stores_pending_row(session):
    client = FakeClient()
    url = _run(begin_connection(session, client, "santander", redirect_url="https://hub/cb"))
    assert url == "https://bank/x"
    conn = session.query(BankConnection).one()
    assert conn.status == BankConnectionStatus.PENDING
    assert conn.bank_name == "Santander Totta" and conn.country == "PT"
    assert client.start_auth_kwargs["state"] == conn.state
    assert client.start_auth_kwargs["redirect_url"] == "https://hub/cb"


def test_complete_connection_activates_and_creates_links(session):
    client = FakeClient()
    _run(begin_connection(session, client, "santander", redirect_url="https://hub/cb"))
    state = session.query(BankConnection).one().state
    conn = _run(complete_connection(session, client, state=state, code="code-1"))
    assert conn.status == BankConnectionStatus.ACTIVE
    assert conn.session_id == "sess"
    assert conn.authorized_at is not None
    assert conn.valid_until == datetime(2027, 3, 1)  # SQLite stores naive UTC
    links = session.query(BankAccountLink).all()
    assert len(links) == 1 and links[0].bank_account_uid == "u1"
    assert links[0].iban == "PT50123"


def test_complete_connection_with_wrong_state_raises(session):
    client = FakeClient()
    begin_connection(session, client, "santander", redirect_url="https://hub/cb")
    with pytest.raises(ValueError):
        _run(complete_connection(session, client, state="wrong", code="code-1"))


def test_complete_connection_twice_replays_are_rejected(session):
    client = FakeClient()
    _run(begin_connection(session, client, "santander", redirect_url="https://hub/cb"))
    state = session.query(BankConnection).one().state
    _run(complete_connection(session, client, state=state, code="code-1"))
    with pytest.raises(ValueError):
        _run(complete_connection(session, client, state=state, code="code-1"))


def test_iban_auto_match_fills_account_id(session):
    client = FakeClient()
    acct = Account(name="Conta Santander", institution="Santander", identifier="PT50123")
    session.add(acct); session.commit()
    _run(begin_connection(session, client, "santander", redirect_url="https://hub/cb"))
    state = session.query(BankConnection).one().state
    _run(complete_connection(session, client, state=state, code="code-1"))
    link = session.query(BankAccountLink).one()
    assert link.account_id == acct.id


def test_iban_without_matching_account_leaves_account_id_null(session):
    client = FakeClient()
    _run(begin_connection(session, client, "santander", redirect_url="https://hub/cb"))
    state = session.query(BankConnection).one().state
    _run(complete_connection(session, client, state=state, code="code-1"))
    assert session.query(BankAccountLink).one().account_id is None


def test_connection_days_left(session):
    conn = BankConnection(bank_name="Santander Totta", country="PT", state="s",
                          status=BankConnectionStatus.ACTIVE)
    session.add(conn); session.commit()
    now = datetime(2027, 2, 1, 12, 0, tzinfo=timezone.utc)
    conn.valid_until = datetime(2027, 3, 1, 13, 0, tzinfo=timezone.utc)
    assert connection_days_left(conn, now=now) == 28


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_renewal_does_not_map_an_ibanless_account_from_a_same_named_iban_account(session):
    """Live bug 2026-10-04: a person's current account (with IBAN) and credit
    card (no IBAN) share the holder's name; the card must NOT inherit the
    current account's mapping on renewal."""
    old = BankConnection(bank_name="Santander Totta", country="PT", state="old",
                         status=BankConnectionStatus.EXPIRED)
    session.add(old); session.commit(); session.refresh(old)
    acct = Account(name="Conta Santander", institution="Santander"); session.add(acct); session.commit()
    session.add(BankAccountLink(connection_id=old.id, bank_account_uid="o1", iban="PT50123",
                                display_name="PEDRO", account_id=acct.id))
    session.commit()

    class TwoAccountsClient(FakeClient):
        async def create_session(self, code):
            return BankSession("sess2", [
                BankSessionAccount("n1", None, "PEDRO", "EUR"),        # the card
                BankSessionAccount("n2", "PT50123", "PEDRO", "EUR"),   # the current account
            ], datetime(2027, 3, 1, tzinfo=timezone.utc))

    client = TwoAccountsClient()
    _run(begin_connection(session, client, "santander", redirect_url="https://hub/cb"))
    new_state = session.query(BankConnection).filter(BankConnection.state != "old").one().state
    _run(complete_connection(session, client, state=new_state, code="c"))
    by_uid = {l.bank_account_uid: l for l in session.query(BankAccountLink)}
    assert by_uid["n1"].account_id is None          # card stays unmapped
    assert by_uid["n2"].account_id == acct.id       # IBAN account carries over
