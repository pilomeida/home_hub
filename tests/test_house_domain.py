from datetime import date

import pytest
from sqlmodel import select

import app.domains.house.handler as house_handler
from app.domains import registry
from app.domains.fields import InvalidClassification, load_fields
from app.models.document import DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.todo import Todo
from app.models.wiki import ClaimStatus, WikiClaim, WikiLogEntry, WikiOperation, WikiPage
from app.services.ingestion import Classification, IncomingFile, ingest, update_document_fields


@pytest.fixture(autouse=True)
def documents_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "docs")


@pytest.fixture()
def extraction(monkeypatch):
    calls = []
    result = {"value": None, "error": None}

    async def fake_extract(file_path, client=None):
        calls.append(file_path)
        if result["error"]:
            raise result["error"]
        return result["value"]

    monkeypatch.setattr(house_handler, "extract_warranty_expiry", fake_extract)
    return calls, result


async def _ingest(session, category, fields, filename="doc.pdf", content=b"bytes"):
    result = await ingest(
        session, IncomingFile(filename, content, DocumentSource.MANUAL), Classification(Domain.HOUSE, category, fields),
    )
    return result.document


def _active(session, page_id):
    return {c.key: c.value for c in session.exec(
        select(WikiClaim).where(WikiClaim.page_id == page_id, WikiClaim.status == ClaimStatus.ACTIVE)
    ).all()}


def test_registry_lists_financials_then_house():
    assert [s.domain for s in registry.implemented_domains()] == [Domain.FINANCIALS, Domain.HOUSE]
    spec = registry.get_spec(Domain.HOUSE)
    assert spec.home_url == "/house"
    assert [c.value for c in spec.categories] == [
        "house_appliance", "outdoor_gear", "warranty_invoice", "maintenance_log", "floor_plan", "ownership_document",
    ]


@pytest.mark.asyncio
async def test_appliance_manual_is_stored_tagged_and_builds_the_item_page(session, extraction):
    document = await _ingest(session, "house_appliance", {"item_name": "Boiler", "room": "Kitchen"})

    assert document.status == DocumentStatus.PROCESSED and document.failure_reason is None
    page = session.exec(select(WikiPage).where(WikiPage.page_type == "house.item")).one()
    assert page.topic == "Boiler" and page.domain == Domain.HOUSE
    assert _active(session, page.id) == {"type": "House appliance", "room": "Kitchen"}
    assert extraction[0] == []  # no LLM for a manual


@pytest.mark.asyncio
async def test_warranty_expiry_is_extracted_reminded_and_recorded(session, extraction):
    calls, result = extraction
    result["value"] = date(2030, 3, 1)

    document = await _ingest(session, "warranty_invoice", {"item_name": "Boiler"})

    assert len(calls) == 1
    assert load_fields(document)["warranty_expiry"] == "2030-03-01"
    todo = session.exec(select(Todo)).one()
    assert todo.title == "Renew Boiler warranty" and todo.due_date == date(2030, 2, 8) and todo.domain == Domain.HOUSE
    page = session.exec(select(WikiPage).where(WikiPage.entity_key == "boiler")).one()
    assert _active(session, page.id)["warranty_expires"] == "2030-03-01"


@pytest.mark.asyncio
async def test_manually_entered_expiry_skips_extraction(session, extraction):
    calls, _ = extraction
    await _ingest(session, "warranty_invoice", {"item_name": "Boiler", "warranty_expiry": "2030-01-01"})
    assert calls == []


@pytest.mark.asyncio
async def test_missing_expiry_is_flagged_then_fixed_by_editing(session, extraction):
    document = await _ingest(session, "warranty_invoice", {"item_name": "Boiler"})
    assert document.status == DocumentStatus.PROCESSED
    assert "enter it manually" in document.failure_reason
    assert session.exec(select(Todo)).all() == []

    await update_document_fields(session, document, {"item_name": "Boiler", "warranty_expiry": "2031-06-30"})

    session.refresh(document)
    assert document.failure_reason is None
    assert session.exec(select(Todo)).one().due_date == date(2031, 6, 9)
    edit_entries = session.exec(select(WikiLogEntry).where(WikiLogEntry.operation == WikiOperation.EDIT)).all()
    assert len(edit_entries) == 1


@pytest.mark.asyncio
async def test_extraction_failure_is_a_note_not_a_failure(session, extraction):
    _, result = extraction
    result["error"] = RuntimeError("API timeout")
    document = await _ingest(session, "warranty_invoice", {"item_name": "Boiler"})
    assert document.status == DocumentStatus.PROCESSED
    assert "API timeout" in document.failure_reason


@pytest.mark.asyncio
async def test_correcting_the_expiry_supersedes_the_old_claim_and_moves_the_reminder(session, extraction):
    document = await _ingest(session, "warranty_invoice", {"item_name": "Boiler", "warranty_expiry": "2030-03-01"})

    await update_document_fields(session, document, {"item_name": "Boiler", "warranty_expiry": "2029-03-01"})

    page = session.exec(select(WikiPage).where(WikiPage.entity_key == "boiler")).one()
    assert _active(session, page.id)["warranty_expires"] == "2029-03-01"
    superseded = session.exec(select(WikiClaim).where(WikiClaim.status == ClaimStatus.SUPERSEDED)).all()
    assert [c.value for c in superseded] == ["2030-03-01"]
    todos = session.exec(select(Todo)).all()
    assert len(todos) == 1 and todos[0].due_date == date(2029, 2, 8)


@pytest.mark.asyncio
async def test_an_older_maintenance_log_does_not_replace_last_serviced(session, extraction):
    await _ingest(session, "maintenance_log", {"item_name": "Boiler", "service_date": "2026-05-01"}, content=b"a")
    await _ingest(session, "maintenance_log", {"item_name": "Boiler", "service_date": "2024-05-01"}, content=b"b")
    page = session.exec(select(WikiPage).where(WikiPage.entity_key == "boiler")).one()
    assert _active(session, page.id)["last_serviced"] == "2026-05-01"


@pytest.mark.asyncio
async def test_floor_plan_rules(session, extraction):
    with pytest.raises(InvalidClassification) as missing:
        await _ingest(session, "floor_plan", {})
    assert set(missing.value.errors) == {"system_type", "indoor_outdoor"}

    pdf = await _ingest(session, "floor_plan",
                        {"system_type": "Pipes", "indoor_outdoor": "outdoor", "observations": "ignored for PDFs"},
                        content=b"pdf")
    video = await _ingest(session, "floor_plan",
                          {"system_type": "Pipes", "indoor_outdoor": "indoor", "observations": "Main valve behind panel"},
                          filename="valve.mp4", content=b"video")

    assert load_fields(pdf) == {"indoor_outdoor": "outdoor", "system_type": "Pipes"}
    assert load_fields(video)["observations"] == "Main valve behind panel"
    assert session.exec(select(WikiPage)).all() == []  # reference documents make no item pages


@pytest.mark.asyncio
async def test_video_is_rejected_for_an_appliance_manual(session, extraction):
    with pytest.raises(InvalidClassification) as exc:
        await _ingest(session, "house_appliance", {"item_name": "Boiler"}, filename="demo.mp4")
    assert "file" in exc.value.errors


@pytest.mark.asyncio
async def test_item_and_room_pages_link_to_each_other(session, extraction):
    from app.services.wiki_store import links_from

    await _ingest(session, "house_appliance", {"item_name": "Boiler", "room": "Kitchen"})

    item = session.exec(select(WikiPage).where(WikiPage.page_type == "house.item")).one()
    room = session.exec(select(WikiPage).where(WikiPage.page_type == "house.room")).one()
    assert room.topic == "Kitchen" and room.entity_key == "kitchen"
    assert [p.id for p in links_from(session, item.id)] == [room.id]
    assert [p.id for p in links_from(session, room.id)] == [item.id]


@pytest.mark.asyncio
async def test_every_processed_house_document_has_an_ingest_log_entry(session, extraction):
    document = await _ingest(session, "ownership_document", {}, filename="deed.pdf")
    entries = session.exec(select(WikiLogEntry).where(WikiLogEntry.document_id == document.id,
                                                      WikiLogEntry.operation == WikiOperation.INGEST)).all()
    assert len(entries) == 1
