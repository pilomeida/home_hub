"""A thin Google Calendar client (REST over httpx, no extra dependency). One refresh token, minted once by
scripts/google_calendar_auth.py, scope calendar.events. Events are found again by a private extended
property `hub_key`, so re-syncing updates instead of duplicating."""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

import httpx

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API = "https://www.googleapis.com/calendar/v3/calendars"


class CalendarError(Exception):
    pass


class GoogleCalendar:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, calendar_id: str,
                 transport: Optional[httpx.AsyncBaseTransport] = None):
        self._creds = (client_id, client_secret, refresh_token)
        self._calendar_id = calendar_id
        self._transport = transport

    @classmethod
    def from_settings(cls, settings) -> Optional["GoogleCalendar"]:
        if not settings.calendar_configured:
            return None
        return cls(settings.GOOGLE_OAUTH_CLIENT_ID, settings.GOOGLE_OAUTH_CLIENT_SECRET,
                   settings.GOOGLE_OAUTH_REFRESH_TOKEN, settings.GOOGLE_CALENDAR_ID)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=20)

    async def _token(self, client: httpx.AsyncClient) -> str:
        cid, secret, refresh = self._creds
        resp = await client.post(_TOKEN_URL, data={"client_id": cid, "client_secret": secret,
                                                   "refresh_token": refresh, "grant_type": "refresh_token"})
        if resp.status_code != 200:
            raise CalendarError(f"Google Calendar refused the sign-in (HTTP {resp.status_code}).")
        return resp.json()["access_token"]

    @property
    def _events_url(self) -> str:
        return f"{_API}/{quote(self._calendar_id, safe='')}/events"

    async def upsert_event(self, key: str, body: dict) -> str:
        body = {**body, "extendedProperties": {"private": {"hub_key": key}}}
        async with self._client() as client:
            headers = {"Authorization": f"Bearer {await self._token(client)}"}
            found = await client.get(self._events_url, headers=headers,
                                     params={"privateExtendedProperty": f"hub_key={key}", "maxResults": 1})
            if found.status_code != 200:
                raise CalendarError(f"Google Calendar could not be read (HTTP {found.status_code}).")
            items = found.json().get("items") or []
            if items:
                resp = await client.patch(f"{self._events_url}/{items[0]['id']}", headers=headers, json=body)
            else:
                resp = await client.post(self._events_url, headers=headers, json=body)
            if resp.status_code not in (200, 201):
                raise CalendarError(f"Google Calendar did not save the event (HTTP {resp.status_code}).")
            return items[0]["id"] if items else resp.json()["id"]   # an update keeps the id it already has

    async def delete_event(self, event_id: str) -> None:
        async with self._client() as client:
            headers = {"Authorization": f"Bearer {await self._token(client)}"}
            resp = await client.delete(f"{self._events_url}/{event_id}", headers=headers)
            if resp.status_code not in (200, 204, 404, 410):
                raise CalendarError(f"Google Calendar did not remove the event (HTTP {resp.status_code}).")
