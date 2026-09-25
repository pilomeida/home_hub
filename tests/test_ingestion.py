import pytest

from app.domains.fields import InvalidClassification, load_fields
from app.models.document import DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.services import ingestion
from app.services.ingestion import (
    AlreadyFinalizedError, Classification, IncomingFile, NotFinalizedError,
)
from tests.domain_fakes import RecordingHandler, make_fake_spec


@pytest.fixture()
def documents_dir(tmp_path, monkeypatch):
    directory = tmp_path / "docs"
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", directory)
    return directory


def _incoming(name="manual.pdf", content=b"pdf-bytes"):
    return IncomingFile(filename=name, content=content, source=DocumentSource.MANUAL, uploaded_by="pedro@example.com")


def test_receive_file_stores_an_unclassified_pending_document(session, documents_dir):
    result = ingestion.receive_file(session, _incoming())

    assert result.duplicate is False
    document = result.document
    assert document.id is not None
    assert document.status == DocumentStatus.PENDING
    assert document.domain is None and document.category is None
    assert load_fields(document) == {}
    assert document.uploaded_by == "pedro@example.com"
    assert len(list(documents_dir.iterdir())) == 1


def test_receive_file_duplicate_returns_existing_without_writing(session, documents_dir):
    first = ingestion.receive_file(session, _incoming())
    second = ingestion.receive_file(session, _incoming(name="renamed.pdf"))

    assert second.duplicate is True
    assert second.document.id == first.document.id
    assert len(list(documents_dir.iterdir())) == 1


def test_receive_file_honours_initial_status(session, documents_dir):
    # Plan B passes its own review status here; NEEDS_ATTENTION stands in for it.
    result = ingestion.receive_file(session, _incoming(), initial_status=DocumentStatus.NEEDS_ATTENTION)
    assert result.document.status == DocumentStatus.NEEDS_ATTENTION


@pytest.mark.asyncio
async def test_finalize_tags_document_and_dispatches_to_handler(session, documents_dir, fake_domain):
    document = ingestion.receive_file(session, _incoming()).document

    result = await ingestion.finalize_document(
        session, document, Classification(Domain.HOUSE, "manual", {"item_name": "Boiler", "junk": "x"}),
    )

    assert result.status == DocumentStatus.PROCESSED
    assert result.domain == Domain.HOUSE
    assert result.category == "manual"
    assert load_fields(result) == {"item_name": "Boiler"}
    assert fake_domain.handler.processed == [document.id]


@pytest.mark.asyncio
async def test_finalize_rejects_invalid_classification_without_mutating(session, documents_dir, fake_domain):
    document = ingestion.receive_file(session, _incoming()).document

    with pytest.raises(InvalidClassification) as unknown_category:
        await ingestion.finalize_document(session, document, Classification(Domain.HOUSE, "nope", {}))
    assert "category" in unknown_category.value.errors

    with pytest.raises(InvalidClassification) as missing_category:
        await ingestion.finalize_document(session, document, Classification(Domain.HOUSE, None, {}))
    assert "category" in missing_category.value.errors

    with pytest.raises(InvalidClassification) as unknown_domain:
        await ingestion.finalize_document(session, document, Classification(Domain.FINANCIALS, "bill", {}))
    assert "domain" in unknown_domain.value.errors

    session.refresh(document)
    assert document.domain is None and document.status == DocumentStatus.PENDING
    assert fake_domain.handler.processed == []


@pytest.mark.asyncio
async def test_finalize_rejects_a_file_type_the_category_does_not_accept(session, documents_dir, fake_domain):
    document = ingestion.receive_file(session, _incoming(name="walkthrough.mp4")).document
    with pytest.raises(InvalidClassification) as exc:
        await ingestion.finalize_document(session, document, Classification(Domain.HOUSE, "manual", {"item_name": "Boiler"}))
    assert "file" in exc.value.errors


@pytest.mark.asyncio
async def test_finalize_refuses_an_already_processed_document(session, documents_dir, fake_domain):
    document = ingestion.receive_file(session, _incoming()).document
    await ingestion.finalize_document(session, document, Classification(Domain.HOUSE, "manual", {"item_name": "Boiler"}))
    with pytest.raises(AlreadyFinalizedError):
        await ingestion.finalize_document(session, document, Classification(Domain.HOUSE, "manual", {"item_name": "Boiler"}))


@pytest.mark.asyncio
async def test_finalize_marks_needs_attention_when_handler_raises(session, documents_dir, monkeypatch):
    from app.domains import registry

    spec = make_fake_spec(handler=RecordingHandler(fail_with=RuntimeError("boom")))
    monkeypatch.setattr(registry, "_specs_cache", {spec.domain: spec})
    document = ingestion.receive_file(session, _incoming()).document

    result = await ingestion.finalize_document(session, document, Classification(Domain.HOUSE, "manual", {"item_name": "Boiler"}))

    assert result.status == DocumentStatus.NEEDS_ATTENTION
    assert "boom" in result.failure_reason
    assert result.domain == Domain.HOUSE  # still finalized: it belongs to the domain, it just failed


@pytest.mark.asyncio
async def test_finalize_allows_missing_category_when_domain_infers_it(session, documents_dir, monkeypatch):
    from app.domains import registry

    spec = make_fake_spec(infers_category=True)
    monkeypatch.setattr(registry, "_specs_cache", {spec.domain: spec})
    document = ingestion.receive_file(session, _incoming()).document

    result = await ingestion.finalize_document(session, document, Classification(Domain.HOUSE))

    assert result.status == DocumentStatus.PROCESSED
    assert result.category is None


@pytest.mark.asyncio
async def test_update_document_fields_validates_saves_and_notifies_handler(session, documents_dir, fake_domain):
    document = ingestion.receive_file(session, _incoming()).document
    await ingestion.finalize_document(session, document, Classification(Domain.HOUSE, "manual", {"item_name": "Boiler"}))

    updated = await ingestion.update_document_fields(session, document, {"item_name": "Boiler", "seen_on": "2026-05-01"})

    assert load_fields(updated) == {"item_name": "Boiler", "seen_on": "2026-05-01"}
    assert fake_domain.handler.changed == [(document.id, {"item_name": "Boiler"})]
    with pytest.raises(InvalidClassification):
        await ingestion.update_document_fields(session, document, {"item_name": ""})


@pytest.mark.asyncio
async def test_update_document_fields_requires_a_finalized_document(session, documents_dir, fake_domain):
    document = ingestion.receive_file(session, _incoming()).document
    with pytest.raises(NotFinalizedError):
        await ingestion.update_document_fields(session, document, {"item_name": "Boiler"})


@pytest.mark.asyncio
async def test_ingest_validates_first_then_receives_and_finalizes(session, documents_dir, fake_domain):
    with pytest.raises(InvalidClassification):
        await ingestion.ingest(session, _incoming(), Classification(Domain.HOUSE, "manual", {}))
    assert not documents_dir.exists() or list(documents_dir.iterdir()) == []

    first = await ingestion.ingest(session, _incoming(), Classification(Domain.HOUSE, "manual", {"item_name": "Boiler"}))
    again = await ingestion.ingest(session, _incoming(), Classification(Domain.HOUSE, "manual", {"item_name": "Boiler"}))

    assert first.duplicate is False and first.document.status == DocumentStatus.PROCESSED
    assert again.duplicate is True and again.document.id == first.document.id
    assert fake_domain.handler.processed == [first.document.id]