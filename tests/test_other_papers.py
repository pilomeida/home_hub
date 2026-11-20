"""PLAN step 1c: "Other papers", the catch-all. Its reader writes title, date, summary and who or what the paper is
about; the Inbox offers it when nothing else fits. Done when (step 1): an unknown paper is uploaded, approved and
found by a word inside it, from the search and from Ask."""

import io
import json

import pytest
from sqlmodel import select

from app.categories.fields import load_fields
from app.categories.registry import get_spec
from app.llm_gateway import set_gateway_provider
from app.models.category import Category
from app.models.document import Document, DocumentSource, DocumentStatus
from app.services import category_classifier, inbox_service, ingestion, paper_search
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


def test_other_papers_is_registered_as_the_catch_all_suggested_mid():
    spec = get_spec(Category.OTHER)
    assert spec.label == "Other papers" and spec.catch_all and spec.home_url == "/other"
    assert [k.value for k in spec.kinds] == ["other_paper", "contract", "letter"]           # step 8b
    assert all(k.suggested_tier.value == "house_mid" for k in spec.kinds)


@pytest.mark.asyncio
async def test_the_reader_uses_the_pdfs_text_and_fills_only_empty_details(session, inbox_documents_dir, gateway):
    gateway._bodies.append(gateway_text_result(json.dumps(READING)))
    doc = (await ingestion.ingest(session, IncomingFile("letter.pdf", text_pdf_bytes(LETTER), DocumentSource.MANUAL),
                                  Classification(Category.OTHER, "other_paper", {"about": "Pedro"}))).document
    assert doc.status == DocumentStatus.PROCESSED
    request = gateway.requests[0]
    assert request["attachments"] is None and "88412-PT" in request["user"]          # the text, not the file
    fields = load_fields(doc)
    assert fields["title"] == READING["title"] and fields["paper_date"] == "2025-03-12"
    assert fields["about"] == "Pedro"                                                 # typed by a person: kept
    assert get_spec(Category.OTHER).paper_date(session, doc).isoformat() == "2025-03-12"


@pytest.mark.asyncio
async def test_a_photo_is_read_from_the_page(session, inbox_documents_dir, gateway):
    gateway._bodies.append(gateway_text_result(json.dumps({**READING, "date": None})))
    doc = (await ingestion.ingest(session, IncomingFile("letter.png", b"\x89PNG\r\n\x1a\nimg", DocumentSource.MANUAL),
                                  Classification(Category.OTHER, "other_paper", {}))).document
    assert gateway.requests[0]["attachments"][0]["type"] == "image"
    assert load_fields(doc).get("paper_date") is None or load_fields(doc).get("paper_date") == ""


@pytest.mark.asyncio
async def test_a_reading_that_fails_leaves_the_paper_for_a_person(session, inbox_documents_dir, gateway):
    gateway._bodies.append(gateway_text_result("not json"))
    doc = (await ingestion.ingest(session, IncomingFile("letter.pdf", text_pdf_bytes(LETTER), DocumentSource.MANUAL),
                                  Classification(Category.OTHER, "other_paper", {}))).document
    assert doc.status == DocumentStatus.NEEDS_ATTENTION and doc.failure_reason


@pytest.mark.asyncio
async def test_the_classifier_offers_other_papers_when_nothing_else_fits(tmp_path, levels_not_binding):
    path = tmp_path / "x.pdf"
    path.write_bytes(text_pdf_bytes(LETTER))
    fake = FakeGateway([gateway_text_result(json.dumps({"domain": None, "category": None, "confidence": 0.9,
                                                        "reason": "A bank letter about tax residence."}))])
    suggestion = await category_classifier.suggest_category_and_kind(str(path), gateway=fake)
    assert (suggestion.category, suggestion.paper_kind) == (Category.OTHER, "other_paper")
    assert suggestion.confidence < category_classifier.CONFIDENT_THRESHOLD           # offered, never decided
    assert "fits none of the other areas" in suggestion.note
    assert '"other"' in fake.requests[0]["system"]                                    # and the model can choose it


@pytest.mark.asyncio
async def test_done_when_an_unknown_paper_is_approved_and_found_by_a_word_inside_it(session, inbox_documents_dir, gateway):
    async def classify(file_path, context_text):
        return await category_classifier.suggest_category_and_kind(
            file_path, context_text, gateway=FakeGateway([gateway_text_result(json.dumps({"domain": None}))]))
    receipt = await inbox_service.receive_document(
        session, IncomingFile("scan_0042.pdf", text_pdf_bytes(LETTER), DocumentSource.EMAIL),
        context_text=None, external_ref=None, classify=classify)
    assert receipt.inbox_item.suggested_category == "other" and not inbox_service.is_confident(receipt.inbox_item)
    gateway._bodies.append(gateway_text_result(json.dumps(READING)))
    filed = await inbox_service.approve(session, receipt.document.id, category_value="other", paper_kind="other_paper",
                                        fields={}, reviewed_by="pedro@example.com")
    assert filed.status == DocumentStatus.PROCESSED
    assert [h.document.id for h in paper_search.search_papers(session, "residencia")] == [filed.id]
    out = next(t for t in ask_tools.core_tools() if t.name == "find_sources").run(session, {"query": "residência fiscal"})
    assert f"doc:{filed.id}" in out.text and "Other papers" in out.text


def test_upload_landing_and_detail_pages(client, engine, inbox_documents_dir, gateway):
    gateway._bodies.append(gateway_text_result(json.dumps(READING)))
    response = client.post("/other/upload", data={"title": "", "summary": ""},
                           files={"file": ("letter.pdf", io.BytesIO(text_pdf_bytes(LETTER)), "application/pdf")},
                           follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/other/documents/")
    landing = client.get("/other").text
    assert READING["title"] in landing and "2025-03-12" in landing and "Pedro Almeida" in landing
    detail = client.get(response.headers["location"])
    assert detail.status_code == 200 and "letter.pdf" in detail.text
    assert client.post("/other/upload", data={}).status_code == 400
    assert 'href="/other"' in client.get("/search").text                               # Papers' own row (step 10b)


def test_a_paper_of_another_category_is_not_shown_here(client, session):
    from tests.knowledge_factories import make_document
    doc = make_document(session, category=Category.HOUSE)
    response = client.get(f"/other/documents/{doc.id}")
    assert response.status_code == 404
