import json

import httpx
import pytest

from app.integrations.google_calendar import GoogleCalendar


class Google:
    def __init__(self, existing=None):
        self.calls = []
        self.existing = existing or []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, str(request.url), request.content.decode()))
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"access_token": "AT", "expires_in": 3600})
        if request.method == "GET":
            return httpx.Response(200, json={"items": self.existing})
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, json={"id": "EV1"})

    def calendar(self):
        return GoogleCalendar("cid", "secret", "refresh", "family@group.calendar.google.com",
                              transport=httpx.MockTransport(self.handler))


@pytest.mark.asyncio
async def test_a_new_event_is_created_with_the_hub_key_and_a_bearer_token():
    g = Google()
    event_id = await g.calendar().upsert_event("health-doc-7", {"summary": "x"})
    assert event_id == "EV1"
    token_call = g.calls[0]
    assert "grant_type=refresh_token" in token_call[2] and "refresh" in token_call[2]
    post = next(c for c in g.calls if c[0] == "POST" and "/events" in c[1])
    body = json.loads(post[2])
    assert body["extendedProperties"]["private"]["hub_key"] == "health-doc-7"
    assert "family%40group.calendar.google.com" in post[1] or "family@group.calendar.google.com" in post[1]


@pytest.mark.asyncio
async def test_an_existing_event_for_the_same_key_is_updated_not_duplicated():
    g = Google(existing=[{"id": "OLD"}])
    event_id = await g.calendar().upsert_event("health-doc-7", {"summary": "y"})
    assert event_id == "OLD"
    assert any(c[0] == "PATCH" and c[1].endswith("/events/OLD") for c in g.calls)
    assert not any(c[0] == "POST" and "/events" in c[1] for c in g.calls)


@pytest.mark.asyncio
async def test_delete_is_idempotent_for_a_missing_event():
    def handler(request):
        if "oauth2" in str(request.url):
            return httpx.Response(200, json={"access_token": "AT", "expires_in": 3600})
        return httpx.Response(404)
    cal = GoogleCalendar("cid", "s", "r", "cal", transport=httpx.MockTransport(handler))
    await cal.delete_event("GONE")                              # must not raise


@pytest.mark.asyncio
async def test_a_failure_raises_a_plain_error():
    def handler(request):
        return httpx.Response(401, json={"error": "invalid_grant"})
    cal = GoogleCalendar("cid", "s", "r", "cal", transport=httpx.MockTransport(handler))
    with pytest.raises(Exception) as exc:
        await cal.upsert_event("k", {})
    assert "calendar" in str(exc.value).lower()
