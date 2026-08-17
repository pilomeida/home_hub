import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import AccessTokenError, CloudflareAccessMiddleware, HEADER_NAME, verify_access_token


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


class _FakeJWKSClient:
    def __init__(self, public_key):
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey(self._public_key)


def _make_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _sign_token(private_key, claims):
    return jwt.encode(claims, private_key, algorithm="RS256")


def test_verify_access_token_accepts_valid_token():
    private_key, public_key = _make_keypair()
    claims = {"email": "pedro@example.com", "aud": "test-aud", "exp": int(time.time()) + 3600}
    token = _sign_token(private_key, claims)

    result = verify_access_token(token, _FakeJWKSClient(public_key), audience="test-aud")

    assert result["email"] == "pedro@example.com"


def test_verify_access_token_rejects_wrong_audience():
    private_key, public_key = _make_keypair()
    claims = {"email": "pedro@example.com", "aud": "other-aud", "exp": int(time.time()) + 3600}
    token = _sign_token(private_key, claims)

    with pytest.raises(AccessTokenError):
        verify_access_token(token, _FakeJWKSClient(public_key), audience="test-aud")


def test_verify_access_token_rejects_expired_token():
    private_key, public_key = _make_keypair()
    claims = {"email": "pedro@example.com", "aud": "test-aud", "exp": int(time.time()) - 10}
    token = _sign_token(private_key, claims)

    with pytest.raises(AccessTokenError):
        verify_access_token(token, _FakeJWKSClient(public_key), audience="test-aud")


def test_middleware_blocks_missing_token():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    _, public_key = _make_keypair()
    test_app = FastAPI()

    @test_app.get("/protected")
    async def protected():
        return {"ok": True}

    test_app.add_middleware(
        CloudflareAccessMiddleware, jwks_client=_FakeJWKSClient(public_key), audience="test-aud"
    )
    client = TestClient(test_app)

    response = client.get("/protected")

    assert response.status_code == 401


def test_middleware_allows_valid_token_and_exposes_email():
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    private_key, public_key = _make_keypair()
    test_app = FastAPI()

    @test_app.get("/protected")
    async def protected(request: Request):
        return {"email": request.state.user_email}

    test_app.add_middleware(
        CloudflareAccessMiddleware, jwks_client=_FakeJWKSClient(public_key), audience="test-aud"
    )
    client = TestClient(test_app)

    token = _sign_token(
        private_key, {"email": "pedro@example.com", "aud": "test-aud", "exp": int(time.time()) + 3600}
    )

    response = client.get("/protected", headers={HEADER_NAME: token})

    assert response.status_code == 200
    assert response.json() == {"email": "pedro@example.com"}


def test_middleware_exempts_health_endpoint():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    _, public_key = _make_keypair()
    test_app = FastAPI()

    @test_app.get("/health")
    async def health():
        return {"status": "ok"}

    test_app.add_middleware(
        CloudflareAccessMiddleware, jwks_client=_FakeJWKSClient(public_key), audience="test-aud"
    )
    client = TestClient(test_app)

    response = client.get("/health")

    assert response.status_code == 200
