from pathlib import Path

import pytest
from sqlmodel import select

from app.domains.fields import InvalidClassification, load_fields
from app.models.document import DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.inbox_item import InboxItem
from app.models.wiki import WikiClaim, WikiLogEntry, WikiOperation, WikiPage
from app.services import inbox_service
from app.services.domain_classifier import DomainSuggestion
from app.services.ingestion import IncomingFile

pytestmark = pytest.mark.usefixtures("two_domains", "inbox_documents_dir")


def _classifier(suggestion):
    async def classify(file_path, context_text=None):
        return suggestion
    return classify


async def _receive(session, content=b"%PDF fake", suggestion=None, classify=None, filename="warranty.pdf"):
    return await inbox_service.receive_document(
        session,
        IncomingFile(filename=filename, content=content, source=DocumentSource.TELEGRAM, uploaded_by="Rute (Telegram)"),
        context_text="Telegram caption: boiler", external_ref="telegram:1:1",
        classify=classify or _classifier(suggestion or DomainSuggestion(Domain.HOUSE, "manual", 0.95, "a manual")),
    )


def _review_logs(session):
    return session.exec(select(WikiLogEntry).where(WikiLogEntry.operation == WikiOperation.REVIEW)).all()


@pytest.mark.asyncio
async def test_receive_creates_unfinalized_pending_review_document(session, two_domains):
    receipt = await _receive(session)

    doc = receipt.document
    assert not receipt.duplicate
    assert doc.status == DocumentStatus.PENDING_REVIEW
    assert doc.domain is None and doc.category is None
    assert doc.source == DocumentSource.TELEGRAM and doc.uploaded_by == "Rute (Telegram)"
    item = receipt.inbox_item
    assert (item.suggested_domain, item.suggested_category, item.confidence) == ("house", "manual", 0.95)
    # Nothing finalized, nothing in the wiki (not even a log line), before approval.
    assert two_domains["house"].handler.processed == []
    assert session.exec(select(WikiPage)).all() == []
    assert session.exec(select(WikiClaim)).all() == []
    assert session.exec(select(WikiLogEntry)).all() == []


@pytest.mark.asyncio
async def test_receive_duplicate_returns_existing(session):
    first = await _receive(session)
    second = await _receive(session)
    assert second.duplicate and second.document.id == first.document.id
    assert second.inbox_item.id == first.inbox_item.id
    assert len(session.exec(select(InboxItem)).all()) == 1


@pytest.mark.asyncio
async def test_classifier_failure_still_lands_in_inbox(session):
    async def boom(file_path, context_text=None):
        raise RuntimeError("credit balance too low")

    receipt = await _receive(session, classify=boom)
    assert receipt.document.status == DocumentStatus.PENDING_REVIEW
    assert receipt.inbox_item.suggested_domain is None
    assert "credit balance too low" in receipt.inbox_item.classifier_note


@pytest.mark.asyncio
async def test_pending_entries_confidence_and_labels(session):
    await _receive(session, content=b"a")
    await _receive(session, content=b"b", suggestion=DomainSuggestion(Domain.HOUSE, "clip", 0.4, "?"))
    await _receive(session, content=b"c", suggestion=DomainSuggestion(Domain.FINANCIALS, None, 0.9, "bill-ish"))
    entries = inbox_service.pending_entries(session)
    assert [e.confident for e in entries] == [True, False, True]
    assert [e.suggestion_label for e in entries] == ["Fake › Manual", "Fake › Clip", "Money"]


@pytest.mark.asyncio
async def test_stale_suggested_domain_is_ignored(session):
    receipt = await _receive(session)
    receipt.inbox_item.suggested_domain = "not_a_registered_domain"
    session.add(receipt.inbox_item)
    session.commit()
    entry = inbox_service.pending_entries(session)[0]
    assert entry.confident is False and entry.suggestion_label is None


@pytest.mark.asyncio
async def test_approve_finalizes_with_fields_and_logs_review(session, two_domains):
    receipt = await _receive(session)

    doc = await inbox_service.approve(
        session, receipt.document.id, domain_value="house", category="manual",
        fields={"item_name": "Boiler"}, reviewed_by="pedro@example.com",
    )

    assert doc.domain == Domain.HOUSE and doc.category == "manual"
    assert load_fields(doc) == {"item_name": "Boiler"}
    assert doc.status == DocumentStatus.PROCESSED
    assert two_domains["house"].handler.processed == [doc.id]
    item = inbox_service.item_for(session, doc.id)
    assert item.reviewed_by == "pedro@example.com" and item.reviewed_at is not None
    [log] = _review_logs(session)
    assert log.document_id == doc.id
    assert log.description.startswith("Approved warranty.pdf") and "Fake › Manual" in log.description


@pytest.mark.asyncio
async def test_approve_into_record_category_creates_record_with_file_attached(session, two_domains):
    from app.domains.entries import record_for_document

    receipt = await _receive(session, filename="service-invoice.pdf")
    doc = await inbox_service.approve(
        session, receipt.document.id, domain_value="house", category="visit",
        fields={"item_name": "Boiler", "visit_date": "2026-03-01"}, reviewed_by="pedro@example.com",
    )

    assert doc.domain == Domain.HOUSE and doc.category == "visit" and doc.status == DocumentStatus.PROCESSED
    assert load_fields(doc) == {}                           # fields live on the Record
    record = record_for_document(session, doc)
    assert record is not None and load_fields(record)["visit_date"] == "2026-03-01"
    [log] = _review_logs(session)
    assert "Fake › Visit" in log.description


@pytest.mark.asyncio
async def test_approve_with_inferred_category(session, two_domains):
    receipt = await _receive(session, suggestion=DomainSuggestion(Domain.FINANCIALS, None, 0.9, "x"))
    doc = await inbox_service.approve(session, receipt.document.id, domain_value="financials",
                                      category=None, fields={}, reviewed_by=None)
    assert doc.domain == Domain.FINANCIALS and doc.category is None
    assert two_domains["money"].handler.processed == [doc.id]


@pytest.mark.asyncio
async def test_approve_invalid_leaves_document_untouched(session, two_domains):
    receipt = await _receive(session)
    with pytest.raises(InvalidClassification) as missing_field:
        await inbox_service.approve(session, receipt.document.id, domain_value="house",
                                    category="manual", fields={}, reviewed_by=None)
    assert "item_name" in missing_field.value.errors
    with pytest.raises(InvalidClassification) as bad_domain:
        await inbox_service.approve(session, receipt.document.id, domain_value="health",
                                    category="x", fields={}, reviewed_by=None)
    assert "domain" in bad_domain.value.errors
    with pytest.raises(InvalidClassification) as bad_category:
        await inbox_service.approve(session, receipt.document.id, domain_value="house",
                                    category="bill", fields={}, reviewed_by=None)
    assert "category" in bad_category.value.errors

    session.refresh(receipt.document)
    assert receipt.document.status == DocumentStatus.PENDING_REVIEW and receipt.document.domain is None
    assert two_domains["house"].handler.processed == []
    assert _review_logs(session) == []
    assert inbox_service.item_for(session, receipt.document.id).reviewed_at is None


@pytest.mark.asyncio
async def test_approve_twice_is_refused(session):
    receipt = await _receive(session)
    await inbox_service.approve(session, receipt.document.id, domain_value="house", category="manual",
                                fields={"item_name": "Boiler"}, reviewed_by=None)
    with pytest.raises(inbox_service.InboxError):
        await inbox_service.approve(session, receipt.document.id, domain_value="house", category="manual",
                                    fields={"item_name": "Boiler"}, reviewed_by=None)


@pytest.mark.asyncio
async def test_discard_keeps_row_and_file_and_logs_review(session, two_domains):
    receipt = await _receive(session)
    doc = inbox_service.discard(session, receipt.document.id, reviewed_by="rute@example.com")

    assert doc.status == DocumentStatus.DISCARDED and doc.domain is None
    assert Path(doc.file_path).exists()
    assert two_domains["house"].handler.processed == []
    [log] = session.exec(select(WikiLogEntry)).all()
    assert log.operation == WikiOperation.REVIEW and log.description.startswith("Discarded warranty.pdf")
    assert inbox_service.pending_entries(session) == []
    assert inbox_service.recently_reviewed(session)[0][1].id == doc.id
    with pytest.raises(inbox_service.InboxError):
        inbox_service.discard(session, doc.id, reviewed_by=None)
