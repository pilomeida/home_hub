"""The generic paper (PLAN step 1c as "Other papers"; since step 12a every area and kind Pedro adds): its reader writes
title, date, summary and who or what the paper is about, keeping what a person typed. Done when (step 1, kept): a
paper that fits nothing is approved (now under a new area) and found by a word inside it, from the search and Ask."""

import json

import pytest

from app.categories.fields import load_fields
from app.categories.registry import get_spec
from app.llm_gateway import set_gateway_provider
from app.models.category import Category
from app.models.document import DocumentSource, DocumentStatus
from app.services import category_classifier, inbox_service, ingestion, paper_search, paper_types
from app.services.ask import tools as ask_tools
from app.services.ingestion import Classification, IncomingFile
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.fakes.text_pdf import text_pdf_bytes

LETTER = ["HANDELSBANKEN - Autocertificacao FATCA CRS", "Exmo. Senhor Pedro Almeida, Lisboa 12/03/2025",
          "Solicitamos o envio do formulario de residencia fiscal ate 30/04/2025", "Referencia 88412-PT conta 0042"]
READING = {"title": "Handelsbanken - autocertificação FATCA/CRS", "date": "2025-03-12", "about": "Pedro Almeida",
           "summary": "Pedido de envio do formulário de residência fiscal até 30/04/2025."}


@pytest.fixture()
def gateway():
    fake = FakeGateway()
    set_gateway_provider(lambda: fake)
    yield fake
    set_gateway_provider(None)


@pytest.fixture()
def letters(session):
    """An added area, as an approval in the Inbox makes it."""
    paper_types.add_area(session, "Letters", "Letters from banks and the State.", first_kind="Bank letter", by=None)
    return Category("letters")


def test_an_added_area_is_suggested_mid_and_has_its_pages(letters):
    spec = get_spec(letters)
    assert spec.label == "Letters" and spec.home_url == "/areas/letters"
    assert [k.value for k in spec.kinds] == ["bank_letter"]
    assert all(k.suggested_tier.value == "house_mid" for k in spec.kinds)


@pytest.mark.asyncio
async def test_the_reader_uses_the_pdfs_text_and_fills_only_empty_details(session, inbox_documents_dir, gateway, letters):
    gateway._bodies.append(gateway_text_result(json.dumps(READING)))
    doc = (await ingestion.ingest(session, IncomingFile("letter.pdf", text_pdf_bytes(LETTER), DocumentSource.MANUAL),
                                  Classification(letters, "bank_letter", {"about": "Pedro"}))).document
    assert doc.status == DocumentStatus.PROCESSED
    request = gateway.requests[0]
    assert request["attachments"] is None and "88412-PT" in request["user"]          # the text, not the file
    fields = load_fields(doc)
    assert fields["title"] == READING["title"] and fields["paper_date"] == "2025-03-12"
    assert fields["about"] == "Pedro"                                                 # typed by a person: kept
    assert get_spec(letters).paper_date(session, doc).isoformat() == "2025-03-12"


@pytest.mark.asyncio
async def test_a_photo_is_read_from_the_page(session, inbox_documents_dir, gateway, letters):
    gateway._bodies.append(gateway_text_result(json.dumps({**READING, "date": None})))
    doc = (await ingestion.ingest(session, IncomingFile("letter.png", b"\x89PNG\r\n\x1a\nimg", DocumentSource.MANUAL),
                                  Classification(letters, "bank_letter", {}))).document
    assert gateway.requests[0]["attachments"][0]["type"] == "image"
    assert load_fields(doc).get("paper_date") is None or load_fields(doc).get("paper_date") == ""


@pytest.mark.asyncio
async def test_a_reading_that_fails_leaves_the_paper_for_a_person(session, inbox_documents_dir, gateway, letters):
    gateway._bodies.append(gateway_text_result("not json"))
    doc = (await ingestion.ingest(session, IncomingFile("letter.pdf", text_pdf_bytes(LETTER), DocumentSource.MANUAL),
                                  Classification(letters, "bank_letter", {}))).document
    assert doc.status == DocumentStatus.NEEDS_ATTENTION and doc.failure_reason


@pytest.mark.asyncio
async def test_a_kind_added_to_a_built_in_area_is_read_generically_not_by_the_area(session, inbox_documents_dir,
                                                                                  gateway):
    paper_types.add_kind(session, Category.HOUSE, "Contract", by=None)
    gateway._bodies.append(gateway_text_result(json.dumps(READING)))
    doc = (await ingestion.ingest(session, IncomingFile("c.pdf", text_pdf_bytes(LETTER), DocumentSource.MANUAL),
                                  Classification(Category.HOUSE, "contract", {}))).document
    assert doc.status == DocumentStatus.PROCESSED and load_fields(doc)["title"] == READING["title"]
    assert "household paper" in gateway.requests[0]["system"]                          # the generic reader's prompt


@pytest.mark.asyncio
async def test_the_classifier_offers_nothing_when_nothing_fits(tmp_path, levels_not_binding):
    path = tmp_path / "x.pdf"
    path.write_bytes(text_pdf_bytes(LETTER))
    fake = FakeGateway([gateway_text_result(json.dumps({"domain": None, "category": None, "confidence": 0.9,
                                                        "reason": "A bank letter about tax residence."}))])
    suggestion = await category_classifier.suggest_category_and_kind(str(path), gateway=fake)
    assert (suggestion.category, suggestion.paper_kind) == (None, None)               # no catch-all any more
    assert '"other"' not in fake.requests[0]["system"]


@pytest.mark.asyncio
async def test_done_when_an_unfitting_paper_is_approved_under_a_new_area_and_found_by_a_word_inside_it(
        session, inbox_documents_dir, gateway):
    async def classify(file_path, context_text):
        return await category_classifier.suggest_category_and_kind(
            file_path, context_text, gateway=FakeGateway([gateway_text_result(json.dumps({"domain": None}))]))
    receipt = await inbox_service.receive_document(
        session, IncomingFile("scan_0042.pdf", text_pdf_bytes(LETTER), DocumentSource.EMAIL),
        context_text=None, external_ref=None, classify=classify)
    assert not inbox_service.is_confident(receipt.inbox_item)
    gateway._bodies.append(gateway_text_result(json.dumps(READING)))
    filed = await inbox_service.approve(session, receipt.document.id, category_value=paper_types.NEW,
                                        paper_kind=paper_types.NEW, new_area="Letters", new_kind="Bank letter",
                                        fields={}, reviewed_by="pedro@example.com")
    assert filed.status == DocumentStatus.PROCESSED
    assert [h.document.id for h in paper_search.search_papers(session, "residencia")] == [filed.id]
    out = next(t for t in ask_tools.core_tools() if t.name == "find_sources").run(session, {"query": "residência fiscal"})
    assert f"doc:{filed.id}" in out.text and "Letters" in out.text


@pytest.mark.asyncio
async def test_landing_and_detail_pages(client, session, inbox_documents_dir, gateway, letters):
    gateway._bodies.append(gateway_text_result(json.dumps(READING)))
    doc = (await ingestion.ingest(session, IncomingFile("letter.pdf", text_pdf_bytes(LETTER), DocumentSource.MANUAL),
                                  Classification(letters, "bank_letter", {}))).document
    landing = client.get("/areas/letters").text
    assert READING["title"] in landing and "2025-03-12" in landing and "Pedro Almeida" in landing
    assert "Bank letter" in landing
    detail = client.get(f"/areas/letters/documents/{doc.id}")
    assert detail.status_code == 200 and "letter.pdf" in detail.text
    assert 'href="/areas/letters"' in client.get("/search").text                       # Papers' own row


def test_a_paper_of_another_category_is_not_shown_here(client, session, letters):
    from tests.knowledge_factories import make_document
    doc = make_document(session, category=Category.HOUSE)
    assert client.get(f"/areas/letters/documents/{doc.id}").status_code == 404
    assert client.get("/areas/house").status_code == 404                               # built-in areas have their own
