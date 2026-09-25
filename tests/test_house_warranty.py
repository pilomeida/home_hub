import json
from datetime import date

import pytest
from sqlmodel import select

from app.domains.house.categories import HouseCategory
from app.domains.house.warranty import (
    REMINDER_LEAD_DAYS, WarrantyExtractionError, extract_warranty_expiry, reminder_due_date, sync_warranty_todo,
)
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.todo import Todo


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _FakeMessages:
    def __init__(self, text):
        self._text = text
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeMessage(self._text)


class _FakeClient:
    def __init__(self, text):
        self.messages = _FakeMessages(text)


def _pdf(tmp_path):
    path = tmp_path / "invoice.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    return str(path)


def test_reminder_is_21_days_before_expiry():
    assert REMINDER_LEAD_DAYS == 21
    assert reminder_due_date(date(2027, 3, 1)) == date(2027, 2, 8)


@pytest.mark.asyncio
async def test_extracts_a_stated_expiry(tmp_path):
    client = _FakeClient(json.dumps({"expiry_date": "2028-05-17"}))
    assert await extract_warranty_expiry(_pdf(tmp_path), client=client) == date(2028, 5, 17)
    assert client.messages.calls[0]["model"] == "claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_returns_none_when_no_date_is_stated(tmp_path):
    assert await extract_warranty_expiry(_pdf(tmp_path), client=_FakeClient('{"expiry_date": null}')) is None


@pytest.mark.asyncio
async def test_returns_none_for_file_types_the_model_cannot_read(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")
    assert await extract_warranty_expiry(str(video), client=_FakeClient("unused")) is None


@pytest.mark.asyncio
async def test_raises_on_an_unparseable_reply(tmp_path):
    with pytest.raises(WarrantyExtractionError):
        await extract_warranty_expiry(_pdf(tmp_path), client=_FakeClient("the warranty lasts two years"))


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
