from datetime import datetime

import pytest
from sqlmodel import select

from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.inbox_item import InboxItem

pytestmark = pytest.mark.usefixtures("two_domains")


def _pending(session, *, confident=True, content_hash="h1", filename="boiler.pdf",
             domain="house", category="manual"):
    document = Document(
        filename=filename, file_path=f"/tmp/{content_hash}.pdf", content_hash=content_hash,
        source=DocumentSource.TELEGRAM, status=DocumentStatus.PENDING_REVIEW, uploaded_by="Rute (Telegram)",
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    session.add(InboxItem(
        document_id=document.id, context_text="Telegram caption: new boiler",
        suggested_domain=domain, suggested_category=category,
        confidence=0.95 if confident else 0.3, classifier_note="a manual",
    ))
    session.commit()
    return document


def test_inbox_lists_pending_items_confident_prefilled(client, session):
    _pending(session, confident=True, content_hash="a", filename="boiler.pdf")
    _pending(session, confident=False, content_hash="b", filename="mystery.pdf", category="clip")

    html = client.get("/inbox").text

    assert "boiler.pdf" in html and "mystery.pdf" in html
    assert "Rute (Telegram)" in html and "new boiler" in html
    assert "Best guess: Fake › Clip" in html                  # the uncertain one
    assert 'value="house" selected' in html                   # the confident one, pre-filled
    assert 'name="item_name"' in html                         # domain field inputs rendered


def test_inbox_empty_state_and_nav(client):
    html = client.get("/inbox").text
    assert "Nothing waiting" in html
    assert 'href="/inbox"' in html


def test_fields_partial_follows_chosen_domain_and_category(client, session):
    document = _pending(session)
    money = client.get(f"/inbox/{document.id}/fields", params={"domain": "financials"}).text
    assert 'value="bill"' in money and 'value="statement"' in money and 'value="manual"' not in money
    assert "Let the Hub work it out" in money
    clip = client.get(f"/inbox/{document.id}/fields", params={"domain": "house", "category": "clip"}).text
    assert 'name="side"' in clip and 'name="item_name"' not in clip


def test_approve_finalizes_and_returns_result(client, session, two_domains):
    document = _pending(session)

    response = client.post(f"/inbox/{document.id}/approve",
                           data={"domain": "house", "category": "manual", "item_name": "Boiler"})

    assert response.status_code == 200
    assert "Fake › Manual" in response.text and 'href="/fake/documents/' in response.text
    assert two_domains["house"].handler.processed == [document.id]
    session.expire_all()
    assert session.get(Document, document.id).domain == Domain.HOUSE


def test_approve_into_record_category_links_to_the_record(client, session, two_domains):
    document = _pending(session, filename="service.pdf")
    fields = client.get(f"/inbox/{document.id}/fields", params={"domain": "house", "category": "visit"}).text
    assert 'name="visit_date"' in fields and 'name="item_name"' in fields

    response = client.post(f"/inbox/{document.id}/approve", data={
        "domain": "house", "category": "visit", "item_name": "Boiler", "visit_date": "2026-03-01",
    })

    assert response.status_code == 200
    assert "Fake › Visit" in response.text and 'href="/fake/records/' in response.text


def test_approve_shows_validation_errors(client, session, two_domains):
    document = _pending(session)

    response = client.post(f"/inbox/{document.id}/approve", data={"domain": "house", "category": "manual"})

    assert response.status_code == 422
    assert "Item is required" in response.text
    assert two_domains["house"].handler.processed == []

    bad_category = client.post(f"/inbox/{document.id}/approve", data={"domain": "house", "category": "bill"})
    assert bad_category.status_code == 422 and "Unknown Fake category" in bad_category.text


def test_discard(client, session, two_domains):
    document = _pending(session)
    response = client.post(f"/inbox/{document.id}/discard")
    assert response.status_code == 200 and "Discarded" in response.text
    session.expire_all()
    assert session.get(Document, document.id).status == DocumentStatus.DISCARDED
    assert two_domains["house"].handler.processed == []


def test_inbox_list_shows_a_friendly_message_for_a_raw_classifier_note(client, session):
    document = _pending(session, confident=False)
    item = session.exec(select(InboxItem).where(InboxItem.document_id == document.id)).one()
    item.classifier_note = "Automatic sorting failed: Error code: 401 - {'type': 'error'}"
    session.add(item)
    session.commit()

    html = client.get("/inbox").text

    assert "The AI service couldn't be reached." in html
    assert "Technical details" in html
    assert "Error code: 401" in html  # kept, inside the technical details


def test_approve_result_shows_a_friendly_message_when_processing_fails(client, session, two_domains):
    document = _pending(session)
    two_domains["house"].handler.fail_with = RuntimeError(
        "Error code: 401 - {'type': 'error', 'error': {'type': 'authentication_error'}}"
    )

    response = client.post(f"/inbox/{document.id}/approve",
                           data={"domain": "house", "category": "manual", "item_name": "Boiler"})

    assert response.status_code == 200
    assert "The AI service couldn't be reached." in response.text
    assert "Technical details" in response.text
    assert "authentication_error" in response.text  # kept, inside the technical details


def test_inbox_list_shows_a_friendly_datetime_for_received_at(client, session):
    document = _pending(session)
    item = session.exec(select(InboxItem).where(InboxItem.document_id == document.id)).one()
    item.received_at = datetime(2026, 8, 18, 9, 5, 52, 222395)
    session.add(item)
    session.commit()

    html = client.get("/inbox").text

    assert "18 Aug 2026, 09:05" in html


def test_inbox_recently_handled_shows_a_friendly_datetime_for_reviewed_at(client, session, two_domains):
    document = _pending(session)
    client.post(f"/inbox/{document.id}/discard")
    item = session.exec(select(InboxItem).where(InboxItem.document_id == document.id)).one()
    item.reviewed_at = datetime(2026, 8, 18, 9, 5, 52, 222395)
    session.add(item)
    session.commit()

    html = client.get("/inbox").text

    assert "18 Aug 2026, 09:05" in html


def test_actions_on_missing_or_handled_document(client, session):
    assert client.post("/inbox/9999/discard").status_code == 404
    document = _pending(session)
    client.post(f"/inbox/{document.id}/discard")
    assert client.post(f"/inbox/{document.id}/discard").status_code == 409
