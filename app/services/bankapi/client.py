"""Thin Enable Banking API client. No database access, no business rules."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import httpx
import jwt

BASE_URL = "https://api.enablebanking.com"
_JWT_TTL_SECONDS = 3600


class BankApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message

    @property
    def rate_limited(self) -> bool:
        return self.status == 429 or "RATE_LIMIT" in self.code.upper()


@dataclass(frozen=True)
class AuthStart:
    url: str
    authorization_id: str


@dataclass(frozen=True)
class BankSessionAccount:
    uid: str
    iban: Optional[str]
    name: Optional[str]
    currency: Optional[str]


@dataclass(frozen=True)
class BankSession:
    session_id: str
    accounts: list[BankSessionAccount]
    valid_until: Optional[datetime]


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None


class EnableBankingClient:
    def __init__(self, app_id: str, private_key_pem: bytes, *,
                 transport: Optional[httpx.AsyncBaseTransport] = None):
        self._app_id = app_id
        self._key = private_key_pem
        self._transport = transport

    def _token(self) -> str:
        now = int(time.time())
        return jwt.encode(
            {"iss": "enablebanking.com", "aud": "api.enablebanking.com",
             "iat": now, "exp": now + _JWT_TTL_SECONDS},
            self._key, algorithm="RS256", headers={"typ": "JWT", "kid": self._app_id},
        )

    async def _request(self, method: str, path: str, *, params=None, json=None) -> dict:
        async with httpx.AsyncClient(base_url=BASE_URL, transport=self._transport, timeout=30) as http:
            response = await http.request(
                method, path, params=params, json=json,
                headers={"Authorization": f"Bearer {self._token()}"},
            )
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            raise BankApiError(response.status_code, str(body.get("error", "")), str(body.get("message", response.text[:200])))
        return response.json()

    async def start_auth(self, *, bank_name: str, country: str, valid_until: datetime,
                         state: str, redirect_url: str) -> AuthStart:
        body = await self._request("POST", "/auth", json={
            "access": {"valid_until": valid_until.isoformat()},
            "aspsp": {"name": bank_name, "country": country},
            "state": state, "redirect_url": redirect_url, "psu_type": "personal",
        })
        return AuthStart(url=body["url"], authorization_id=body["authorization_id"])

    async def create_session(self, code: str) -> BankSession:
        body = await self._request("POST", "/sessions", json={"code": code})
        accounts = [
            BankSessionAccount(
                uid=a["uid"], iban=(a.get("account_id") or {}).get("iban"),
                name=a.get("name"), currency=a.get("currency"),
            )
            for a in body.get("accounts", [])
        ]
        return BankSession(body["session_id"], accounts, _parse_dt((body.get("access") or {}).get("valid_until")))

    async def list_transactions(self, account_uid: str, date_from: date, date_to: date) -> list[dict]:
        rows: list[dict] = []
        key: Optional[str] = None
        while True:
            params = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}
            if key:
                params["continuation_key"] = key
            body = await self._request("GET", f"/accounts/{account_uid}/transactions", params=params)
            rows.extend(t for t in body.get("transactions", []) if t.get("status") == "BOOK")
            key = body.get("continuation_key")
            if not key:
                return rows

    async def max_consent_days(self, bank_name: str, country: str) -> int:
        body = await self._request("GET", "/aspsps", params={"country": country})
        for bank in body.get("aspsps", []):
            if bank.get("name") == bank_name:
                return int(bank.get("maximum_consent_validity", 90 * 86400)) // 86400
        raise BankApiError(404, "ASPSP_NOT_FOUND", f"{bank_name} ({country}) not offered by Enable Banking")
