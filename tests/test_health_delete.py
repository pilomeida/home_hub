import json
from datetime import date

import pytest
from sqlmodel import select

from app.domains.health import store
from app.models.document import DocumentStatus
from app.models.health import HealthEntry, HealthMeasurement
from app.models.wiki import ClaimStatus, WikiClaim, WikiClaimSource
from tests.test_health_handler import _file  # files a real paper through ingest()
from tests import privacy_world as pw
from tests.fakes.fake_gateway import gateway_text_result


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr("app.domains.health.appointments._today", lambda: date(2026, 10, 16))


@pytest.mark.asyncio
async def test_discarding_removes_the_derived_data_and_hides_the_paper_but_keeps_the_file(session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "documents")
    wire = pw.Wire()
    doc = (await _file(session, wire)).document
    path = doc.file_path
    assert session.exec(select(HealthMeasurement)).all() and session.exec(select(WikiClaimSource)).all()

    await store.discard_document(session, doc)
    session.refresh(doc)
    assert doc.status == DocumentStatus.DISCARDED
    assert session.exec(select(HealthEntry)).all() == [] and session.exec(select(HealthMeasurement)).all() == []
    # The engine supersedes a claim only this paper supported; a claim others still back keeps them and only this link is withdrawn.
    for link in session.exec(select(WikiClaimSource).where(WikiClaimSource.document_id == doc.id)).all():
        assert link.withdrawn_at is not None or session.get(WikiClaim, link.claim_id).status != ClaimStatus.ACTIVE
    import os
    assert os.path.exists(path)                                     # raw sources are immutable


def test_the_delete_route_redirects_to_the_list(client, session):
    from datetime import date
    from tests.test_health_browse import _paper
    doc = _paper(session, "Ana Lima", date(2026, 1, 1), "test", [], name="d.pdf")
    r = client.post(f"/health/documents/{doc.id}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/health/records"
    assert "d.pdf" not in client.get("/health/records").text


@pytest.mark.asyncio
async def test_a_failed_calendar_delete_names_the_entry_and_the_paper_is_still_discarded(session):
    from app.domains.health.appointments import appointment_event, sync_appointment
    from app.models.schedule import ProblemEntry
    from tests.test_health_appointments import RefusingCalendar, _appointment
    doc = _appointment(session)
    fake = RefusingCalendar()
    await sync_appointment(session, doc, calendar=fake.calendar(), public_url="https://hub.example")
    title = appointment_event(doc, "https://hub.example")["summary"]
    await store.discard_document(session, doc, calendar=fake.calendar())

    session.refresh(doc)
    assert doc.status == DocumentStatus.DISCARDED
    p = session.exec(select(ProblemEntry).where(ProblemEntry.area == "Calendar")).one()
    assert p.what == "Not removed from the calendar." and p.why and p.next_step
    assert title == "Maria Santos Almeida: Clínica Sul" and title in p.next_step
    assert "2026-11-03 at 10:30" in p.next_step
    assert "EV1" in p.technical


@pytest.mark.asyncio
async def test_discarding_an_appointment_also_removes_its_calendar_entry(session):
    from app.domains.health.appointments import sync_appointment
    from tests.test_google_calendar import Google
    from tests.test_health_appointments import _appointment
    doc = _appointment(session)
    g = Google()
    await sync_appointment(session, doc, calendar=g.calendar(), public_url="https://hub.example")
    await store.discard_document(session, doc, calendar=g.calendar())
    assert any(c[0] == "DELETE" for c in g.calls)
    assert doc.status == DocumentStatus.DISCARDED


def test_a_failed_delete_says_what_why_and_what_to_do_instead_of_a_500(client, session, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.models.schedule import ProblemEntry
    from tests.test_health_browse import _paper
    doc = _paper(session, "Ana Lima", date(2026, 1, 1), "test", [], name="d.pdf")

    async def failing(session, source, *, description):
        raise OperationalError("UPDATE wiki_claims", {}, Exception("database is locked"))     # a real error class
    monkeypatch.setattr("app.services.wiki_engine.withdraw_from_wiki", failing)

    r = client.post(f"/health/documents/{doc.id}/delete", follow_redirects=False)
    assert r.status_code == 500
    assert "Not deleted." in r.text and "left as it was" in r.text and "Try again" in r.text
    assert "database is locked" not in r.text and "OperationalError" not in r.text          # no bare exception text
    p = session.exec(select(ProblemEntry)).one()
    assert p.area == "Health" and p.ref == f"document:{doc.id}" and "database is locked" in p.technical
    session.refresh(doc)
    assert doc.status != DocumentStatus.DISCARDED and session.exec(select(HealthEntry)).all()   # nothing half-deleted


def test_health_not_found_messages_are_three_part(client, session):
    from tests.test_health_browse import _paper
    missing = client.get("/health/documents/9999")
    assert missing.status_code == 404
    assert "Paper not found." in missing.text and "Open the Health records list" in missing.text
    doc = _paper(session, "Ana Lima", date(2026, 1, 1), "test", [], name="d.pdf")
    r = client.post(f"/health/documents/{doc.id}/measurements/9999/ok")
    assert r.status_code == 404
    assert "Value not found." in r.text and "Reload the paper" in r.text
