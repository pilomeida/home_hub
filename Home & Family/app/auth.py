"""Cloudflare Access JWT verification."""

from __future__ import annotations

import jwt
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import settings

HEADER_NAME = "Cf-Access-Jwt-Assertion"


class AccessTokenError(Exception):
    pass


def verify_access_token(token: str, jwks_client, audience: str) -> dict:
    """Verify a Cloudflare Access JWT and return its claims.

    Raises AccessTokenError if the token is missing, expired, or has the
    wrong audience/signature.
    """
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(token, signing_key.key, algorithms=["RS256"], audience=audience)
    except jwt.PyJWTError as exc:
        raise AccessTokenError(str(exc)) from exc


class CloudflareAccessMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, jwks_client=None, audience: str | None = None):
        super().__init__(app)
        self._jwks_client = jwks_client or jwt.PyJWKClient(
            f"https://{settings.CF_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs"
        )
        self._audience = audience or settings.CF_ACCESS_AUD

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        token = request.headers.get(HEADER_NAME) or request.cookies.get("CF_Authorization")
        if not token:
            return JSONResponse({"detail": "Missing Cloudflare Access token"}, status_code=401)

        try:
            claims = verify_access_token(token, self._jwks_client, self._audience)
        except AccessTokenError as exc:
            return JSONResponse({"detail": f"Invalid Cloudflare Access token: {exc}"}, status_code=401)

        request.state.user_email = claims.get("email", "unknown")
        return await call_next(request)
