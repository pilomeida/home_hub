import json
from datetime import date

import pytest
from sqlmodel import select

from app.domains.house.categories import HouseCategory
from app.domains.house.warranty import (
    LEGAL_GUARANTEE_YEARS, REMINDER_LEAD_DAYS, WarrantyDates, WarrantyExtractionError,
    effective_warranty_expiry, extract_warranty_dates, house_derived_fields, reminder_due_date, sync_warranty_todo,
)
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.todo import Todo


def _fake_gateway(text):
    """A FakeGateway scripted with one warranty-dates JSON result."""
    from tests.fakes.fake_gateway import FakeGateway

    return FakeGateway([{
        "text": text, "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }])


def _pdf(tmp_path):
    path = tmp_path / "invoice.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    return str(path)


def test_reminder_is_21_days_before_expiry():
    assert REMINDER_LEAD_DAYS == 21
    assert reminder_due_date(date(2027, 3, 1)) == date(2027, 2, 8)


@pytest.mark.asyncio
async def test_extracts_a_stated_expiry(tmp_path):
    client = _fake_gateway(json.dumps({"expiry_date": "2028-05-17", "purchase_date": None}))
    assert await extract_warranty_dates(_pdf(tmp_path), gateway=client) == WarrantyDates(date(2028, 5, 17), None)
    req = client.requests[0]
    assert req["workload_type"] == "vision_extraction"
    assert "model" not in req and req["max_tokens"] is None
    assert len(req["attachments"]) == 1 and req["attachments"][0]["type"] == "document"


@pytest.mark.asyncio
async def test_extracts_expiry_and_purchase_date(tmp_path):
    client = _fake_gateway(json.dumps({"expiry_date": None, "purchase_date": "2026-03-12"}))
    assert await extract_warranty_dates(_pdf(tmp_path), gateway=client) == WarrantyDates(None, date(2026, 3, 12))


@pytest.mark.asyncio
async def test_unreadable_file_types_give_no_dates(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    assert await extract_warranty_dates(str(video), gateway=_fake_gateway("unused")) == WarrantyDates(None, None)


@pytest.mark.asyncio
async def test_unparseable_reply_raises(tmp_path):
    with pytest.raises(WarrantyExtractionError):
        await extract_warranty_dates(_pdf(tmp_path), gateway=_fake_gateway("two years"))


def test_effective_expiry_prefers_stated_then_assumes_legal_guarantee():
    assert LEGAL_GUARANTEE_YEARS == 3
    stated = effective_warranty_expiry({"warranty_expiry": "2028-01-01", "purchase_date": "2026-01-01"})
    assert stated.date == date(2028, 1, 1) and stated.assumed is False
    assumed = effective_warranty_expiry({"purchase_date": "2024-02-29"})
    assert assumed.date == date(2027, 2, 28) and assumed.assumed is True
    assert effective_warranty_expiry({}) is None
    derived = house_derived_fields("warranty_invoice", {"purchase_date": "2026-03-12"})
    assert derived["effective_warranty_expiry"] == "2029-03-12"
    assert "3-year legal guarantee" in derived["warranty_expiry_note"] and "12 Mar 2026" in derived["warranty_expiry_note"]
    assert house_derived_fields("house_appliance", {"purchase_date": "2026-03-12"}) == {}


def _warranty_document(session, fields):
    document = Document(
        filename="w.pdf", file_path="/tmp/w.pdf", content_hash=f"h-{json.dumps(fields)}",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED, domain=Domain.HOUSE,
        category=HouseCategory.WARRANTY_INVOICE.value, fields_json=json.dumps(fields),
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def test_sync_creates_the_renewal_todo(session):
    document = _warranty_document(session, {"item_name": "Boiler", "warranty_expiry": "2027-03-01"})

    todo = sync_warranty_todo(session, document, today=date(2026, 9, 24))

    assert todo.title == "Renew Boiler warranty"
    assert todo.due_date == date(2027, 2, 8)
    assert todo.domain == Domain.HOUSE and todo.document_id == document.id


def test_sync_skips_missing_or_lapsed_expiry_and_other_categories(session):
    assert sync_warranty_todo(session, _warranty_document(session, {"item_name": "Boiler"}), today=date(2026, 9, 24)) is None
    lapsed = _warranty_document(session, {"item_name": "Oven", "warranty_expiry": "2026-01-01"})
    assert sync_warranty_todo(session, lapsed, today=date(2026, 9, 24)) is None
    manual = _warranty_document(session, {"item_name": "Fridge", "warranty_expiry": "2027-01-01"})
    manual.category = HouseCategory.HOUSE_APPLIANCE.value
    assert sync_warranty_todo(session, manual, today=date(2026, 9, 24)) is None
    assert session.exec(select(Todo)).all() == []


def test_sync_uses_an_assumed_expiry(session):
    document = _warranty_document(session, {"item_name": "Oven", "purchase_date": "2026-03-12"})
    todo = sync_warranty_todo(session, document, today=date(2026, 9, 24))
    assert todo.due_date == date(2029, 2, 19)
