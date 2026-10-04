# tests/test_bank_client.py
import json
from datetime import date, datetime, timezone

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services.bankapi.client import BankApiError, EnableBankingClient


@pytest.fixture(scope="module")
def key_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return pem, key.public_key()


def _client(key_pair, handler):
    pem, _ = key_pair
    return EnableBankingClient("app-123", pem, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_requests_carry_a_valid_rs256_jwt(key_pair):
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"url": "https://bank/auth", "authorization_id": "a1"})

    client = _client(key_pair, handler)
    result = await client.start_auth(
        bank_name="Santander", country="PT", valid_until=datetime(2027, 1, 1, tzinfo=timezone.utc),
        state="s1", redirect_url="https://hub/cb",
    )
    assert result.url == "https://bank/auth" and result.authorization_id == "a1"
    token = seen["auth"].removeprefix("Bearer ")
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256" and header["kid"] == "app-123" and header["typ"] == "JWT"
    claims = jwt.decode(token, key_pair[1], algorithms=["RS256"], audience="api.enablebanking.com")
    assert claims["iss"] == "enablebanking.com"
    assert claims["exp"] - claims["iat"] <= 86400


@pytest.mark.asyncio
async def test_list_transactions_follows_pages_and_drops_pending(key_pair):
    def handler(request: httpx.Request):
        key = request.url.params.get("continuation_key")
        if key is None:
            return httpx.Response(200, json={"transactions": [{"entry_reference": "1", "status": "BOOK"},
                                                              {"entry_reference": "2", "status": "PDNG"}],
                                             "continuation_key": "next"})
        return httpx.Response(200, json={"transactions": [{"entry_reference": "3", "status": "BOOK"}]})

    rows = await _client(key_pair, handler).list_transactions("uid", date(2026, 9, 1), date(2026, 10, 1))
    assert [r["entry_reference"] for r in rows] == ["1", "3"]


@pytest.mark.asyncio
async def test_rate_limit_is_flagged(key_pair):
    def handler(request):
        return httpx.Response(429, json={"error": "ASPSP_RATE_LIMIT_EXCEEDED", "message": "slow down"})

    with pytest.raises(BankApiError) as caught:
        await _client(key_pair, handler).list_transactions("uid", date(2026, 9, 1), date(2026, 10, 1))
    assert caught.value.rate_limited is True


@pytest.mark.asyncio
async def test_create_session_parses_accounts(key_pair):
    def handler(request):
        return httpx.Response(200, json={
            "session_id": "sess-1",
            "accounts": [{"uid": "u1", "account_id": {"iban": "PT50000"}, "name": "Conta", "currency": "EUR"}],
            "access": {"valid_until": "2027-03-01T00:00:00Z"},
        })

    session = await _client(key_pair, handler).create_session("code-1")
    assert session.session_id == "sess-1"
    assert session.accounts[0].uid == "u1" and session.accounts[0].iban == "PT50000"
    assert session.valid_until.year == 2027


@pytest.mark.asyncio
async def test_start_auth_always_sends_a_timezone_aware_valid_until(key_pair):
    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"url": "https://bank/auth", "authorization_id": "a1"})

    await _client(key_pair, handler).start_auth(
        bank_name="Santander Totta", country="PT", valid_until=datetime(2027, 4, 2, 12, 0),  # naive
        state="s1", redirect_url="https://hub/cb",
    )
    assert seen["body"]["access"]["valid_until"].endswith("+00:00")
