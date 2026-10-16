import json
from datetime import date

import httpx
import pytest
from sqlmodel import select

from app.domains.health.appointments import appointment_event, remove_appointment, sync_appointment
from app.domains.health.extraction import ExtractedHealth
from app.domains.health.store import apply_extraction, entry_for
from app.models.domain import Domain
from app.models.schedule import ProblemEntry
from tests.knowledge_factories import make_document
from tests.test_google_calendar import Google

SECRET = "SENTINEL-CLINICAL-REASON"
TODAY = date(2026, 10, 16)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    """The sample appointment is on 2026-11-03; paths that reach the calendar through update_document_fields
    read today from the module, so pin it (otherwise these tests would rot on 2026-11-04)."""
    monkeypatch.setattr("app.domains.health.appointments._today", lambda: TODAY)


def _appointment(session, with_time="10:30"):
    doc = make_document(session, domain=Domain.HEALTH, category="appointment", filename="consulta.pdf")
    apply_extraction(session, doc, ExtractedHealth(
        person_name="Maria Santos Almeida", document_date=date(2026, 10, 1), provider="Clínica Sul",
        provider_phone="+351 21 000 0000", provider_address="Av. Central 5, Lisboa", record_kind="follow_up",
        summary=f"{SECRET}: oncology follow-up", appointment_date=date(2026, 11, 3), appointment_time=with_time))
    return doc


def test_the_event_has_when_where_who_contact_a_reminder_and_a_link_but_nothing_clinical(session):
    doc = _appointment(session)
    body = appointment_event(doc, "https://hub.example")
    text = json.dumps(body, ensure_ascii=False)
    assert body["start"] == {"dateTime": "2026-11-03T10:30:00", "timeZone": "Europe/Lisbon"}
    assert body["end"]["dateTime"] == "2026-11-03T11:00:00"
    assert "Clínica Sul" in text and "Maria" in text
    assert body["location"] == "Av. Central 5, Lisboa" and "+351 21 000 0000" in body["description"]
    assert f"https://hub.example/health/documents/{doc.id}" in body["description"]
    assert body["reminders"]["overrides"] == [{"method": "popup", "minutes": 1440}, {"method": "popup", "minutes": 120}]
    assert SECRET not in text and "oncology" not in text and "follow" not in text.lower()


def test_without_a_time_it_is_an_all_day_event(session):
    body = appointment_event(_appointment(session, with_time=None), "https://hub.example")
    assert body["start"] == {"date": "2026-11-03"} and body["end"] == {"date": "2026-11-04"}


def test_only_appointments_with_a_date_get_an_event(session):
    doc = make_document(session, domain=Domain.HEALTH, category="lab_result")
    assert appointment_event(doc, "https://hub.example") is None


@pytest.mark.asyncio
async def test_sync_stores_the_event_id_and_resync_updates_it(session):
    doc = _appointment(session)
    g = Google()
    assert await sync_appointment(session, doc, calendar=g.calendar(), public_url="https://hub.example") == "EV1"
    assert entry_for(session, doc.id).calendar_event_id == "EV1"
    g2 = Google(existing=[{"id": "EV1"}])
    await sync_appointment(session, doc, calendar=g2.calendar(), public_url="https://hub.example")
    assert any(c[0] == "PATCH" for c in g2.calls)


@pytest.mark.asyncio
async def test_remove_deletes_the_event_and_forgets_the_id(session):
    doc = _appointment(session)
    g = Google()
    await sync_appointment(session, doc, calendar=g.calendar(), public_url="https://hub.example")
    await remove_appointment(session, doc, calendar=g.calendar())
    assert any(c[0] == "DELETE" for c in g.calls) and entry_for(session, doc.id).calendar_event_id is None


@pytest.mark.asyncio
async def test_when_the_calendar_is_not_set_up_or_fails_the_paper_is_still_filed_and_the_problem_is_logged(session):
    doc = _appointment(session)
    assert await sync_appointment(session, doc, calendar=None, public_url="https://hub.example") is None
    p = session.exec(select(ProblemEntry)).first()
    assert p.what.startswith("Not added to the calendar") and p.why and p.next_step

    def handler(request):
        return httpx.Response(401, json={})
    from app.integrations.google_calendar import GoogleCalendar
    bad = GoogleCalendar("c", "s", "r", "cal", transport=httpx.MockTransport(handler))
    assert await sync_appointment(session, doc, calendar=bad, public_url="https://hub.example") is None


@pytest.mark.asyncio
async def test_clearing_the_appointment_date_removes_the_calendar_entry_too(session, monkeypatch):
    from app.domains.fields import load_fields
    from app.integrations.google_calendar import GoogleCalendar
    from app.services.ingestion import update_document_fields
    doc = _appointment(session)
    g = Google()
    await sync_appointment(session, doc, calendar=g.calendar(), public_url="https://hub.example")
    assert entry_for(session, doc.id).calendar_event_id == "EV1"

    monkeypatch.setattr(GoogleCalendar, "from_settings", lambda settings: g.calendar())
    fields = load_fields(doc)
    fields["appointment_date"] = ""
    await update_document_fields(session, doc, fields)

    assert any(c[0] == "DELETE" for c in g.calls)
    assert entry_for(session, doc.id).calendar_event_id is None


class RefusingCalendar(Google):
    """Accepts the event but refuses to delete it (HTTP 500)."""

    def handler(self, request):
        if request.method == "DELETE":
            self.calls.append((request.method, str(request.url), ""))
            return httpx.Response(500, json={"error": {"message": "backend unavailable"}})
        return super().handler(request)


@pytest.mark.asyncio
async def test_a_failed_delete_when_the_date_is_cleared_names_the_entry_by_the_date_it_had(session, monkeypatch):
    from app.domains.fields import load_fields
    from app.integrations.google_calendar import GoogleCalendar
    from app.models.schedule import ProblemEntry
    from app.services.ingestion import update_document_fields
    doc = _appointment(session)
    fake = RefusingCalendar()
    await sync_appointment(session, doc, calendar=fake.calendar(), public_url="https://hub.example")
    monkeypatch.setattr(GoogleCalendar, "from_settings", lambda settings: fake.calendar())
    fields = load_fields(doc)
    fields["appointment_date"] = ""
    await update_document_fields(session, doc, fields)

    p = session.exec(select(ProblemEntry).where(ProblemEntry.area == "Calendar")).one()
    assert "Maria Santos Almeida: Clínica Sul" in p.next_step and "2026-11-03 at 10:30" in p.next_step
    assert "EV1" in p.technical
    assert entry_for(session, doc.id).calendar_event_id == "EV1"       # kept, so a later edit can retry


def test_a_past_appointment_gets_no_event_but_today_and_later_do(session):
    doc = _appointment(session)                                       # 2026-11-03
    assert appointment_event(doc, "https://hub.example", today=date(2026, 11, 4)) is None
    assert appointment_event(doc, "https://hub.example", today=date(2026, 11, 3)) is not None
    assert appointment_event(doc, "https://hub.example", today=date(2026, 11, 2)) is not None


@pytest.mark.asyncio
async def test_sync_does_nothing_for_a_past_appointment_and_leaves_an_existing_event_alone(session):
    doc = _appointment(session)
    g = Google()
    assert await sync_appointment(session, doc, calendar=g.calendar(), public_url="https://hub.example",
                                  today=date(2026, 10, 1)) == "EV1"
    calls_before = len(g.calls)
    later = date(2026, 12, 1)
    assert await sync_appointment(session, doc, calendar=g.calendar(), public_url="https://hub.example",
                                  today=later) is None
    assert len(g.calls) == calls_before                               # nothing sent, nothing deleted
    assert entry_for(session, doc.id).calendar_event_id == "EV1"      # the event it already had is left

    old = _appointment(session)
    g2 = Google()
    assert await sync_appointment(session, old, calendar=g2.calendar(), public_url="https://hub.example",
                                  today=later) is None
    assert g2.calls == [] and entry_for(session, old.id).calendar_event_id is None
    assert session.exec(select(ProblemEntry)).all() == []             # a past paper is not a failure
