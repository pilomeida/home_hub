"""PLAN step 12d: approval files the paper under what the Hub read. The paper goes by its new name everywhere (the name
it came in with kept), its tags and who it is about are stored, each item it lists is its own House card; House
filters by room, every people page follows the Who chip; the kind's own fields that repeat the guess are filled
from it instead of asked again.
Done when: the AEG invoice, approved without typing its item, is "2024-03-14 · Warranty invoice · Fatura AEG
eletrodomesticos cozinha.pdf" with three Kitchen cards; the diploma is Pedro's and shows on Education for Pedro only."""

import json

import pytest

from app.llm_gateway import set_gateway_provider
from app.models.category import Category
from app.models.document import Document, PaperItem
from app.models.subject import SubjectKind
from app.services import category_classifier, household, inbox_service, paper_filing, subjects, who
from app.services.ingestion import IncomingFile
from app.models.document import DocumentSource
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.fakes.text_pdf import text_pdf_bytes

AEG = {"who": None, "paper_date": "2024-03-14", "what": "Fatura AEG eletrodomesticos cozinha",
       "tags": ["AEG", "appliances"], "act_by": None,
       "items": [{"name": "Exaustor AEG DGE5861HM", "room": "Kitchen", "new_room": False},
                 {"name": "Micro-ondas AEG MSB2547D-M", "room": "Kitchen", "new_room": False},
                 {"name": "Forno AEG BPK558370M", "room": "Kitchen", "new_room": False}]}
DIPLOMA = {"who": "Pedro", "who_is_family": True, "paper_date": "2002-12-06", "what": "Diploma Gestao TIC Catolica",
           "tags": ["Catolica"], "items": []}
AEG_NAME = "2024-03-14 · Warranty invoice · Fatura AEG eletrodomesticos cozinha"


@pytest.fixture()
def gateway():
    fake = FakeGateway([gateway_text_result(json.dumps({"expiry_date": None, "purchase_date": "2024-03-14"}))] * 3
                       + [gateway_text_result(json.dumps({"student": "", "school": "Catolica", "school_year": "",
                                                          "paper_date": None, "what": "", "date_to_note": None,
                                                          "amount": "", "due_on": None}))] * 3)
    set_gateway_provider(lambda: fake)
    yield fake
    set_gateway_provider(None)


async def _waiting(session, guess, category, kind, filename):
    household.ensure(session)

    async def classify(file_path, context_text):
        return category_classifier.CategorySuggestion(category, kind, 0.5, "A guess.", dict(guess))
    receipt = await inbox_service.receive_document(
        session, IncomingFile(filename, text_pdf_bytes([filename]), DocumentSource.EMAIL),
        context_text=None, external_ref=None, classify=classify)
    return receipt.document


def _guess_form(guess, **extra):
    return {"g_who": guess.get("who") or "", "g_paper_date": guess["paper_date"], "g_what": guess["what"],
            "g_tags": ", ".join(guess["tags"]), "g_item_name": [i["name"] for i in guess["items"]],
            "g_item_room": [i["room"] for i in guess["items"]], "g_item_warranty": [""] * len(guess["items"]), **extra}


@pytest.mark.asyncio
async def test_done_when_the_aeg_invoice_is_filed_under_its_name_with_one_card_per_item(client, session, gateway):
    doc = await _waiting(session, AEG, Category.HOUSE, "warranty_invoice", "AEG Fatura.pdf")
    card = client.get("/inbox").text
    assert 'name="item_name"' not in card and 'name="purchase_date"' not in card      # not asked again
    response = client.post(f"/inbox/{doc.id}/approve",
                           data={"domain": "house", "category": "warranty_invoice", **_guess_form(AEG)})
    assert response.status_code == 200, response.text
    session.expire_all()
    filed = session.get(Document, doc.id)
    assert filed.filename == AEG_NAME + ".pdf" and filed.original_filename == "AEG Fatura.pdf"
    assert json.loads(filed.fields_json)["purchase_date"] == "2024-03-14"            # filled from the guess
    assert paper_filing.tags_of(filed) == ["AEG", "appliances"]
    items = paper_filing.items_of(session, doc.id)
    assert [(i.name, i.room, i.warranty_end) for i in items] == [
        ("Exaustor AEG DGE5861HM", "Kitchen", "2027-03-14"), ("Micro-ondas AEG MSB2547D-M", "Kitchen", "2027-03-14"),
        ("Forno AEG BPK558370M", "Kitchen", "2027-03-14")]
    assert all(subjects.find(session, SubjectKind.THING, i.name) is not None for i in items)
    house = client.get("/house").text
    assert all(i.name in house for i in items) and "Fatura AEG eletrodomesticos cozinha</" not in house
    assert client.get("/house?room=Kitchen").text.count("Exaustor AEG") == 1
    assert "Exaustor AEG" in client.get("/house?room=Garden").text                   # a room with no items: all shown
    page = client.get(f"/house/documents/{doc.id}").text
    assert AEG_NAME in page and "came in as AEG Fatura.pdf" in page


@pytest.mark.asyncio
async def test_a_room_filter_shows_that_rooms_items_only(client, session, gateway):
    doc = await _waiting(session, AEG, Category.HOUSE, "warranty_invoice", "AEG Fatura.pdf")
    guess = {**AEG, "items": [AEG["items"][0], {**AEG["items"][1], "room": "Laundry"}]}
    client.post(f"/inbox/{doc.id}/approve", data={"domain": "house", "category": "warranty_invoice",
                                                  **_guess_form(guess)})
    laundry = client.get("/house?room=laundry").text
    assert "Micro-ondas AEG" in laundry and "Exaustor AEG" not in laundry
    assert "Exaustor AEG" in client.get("/house").text


@pytest.mark.asyncio
async def test_done_when_the_diploma_is_pedros_and_education_follows_the_who_chip(client, session, gateway):
    doc = await _waiting(session, DIPLOMA, Category.EDUCATION, "diploma", "scan0001.pdf")
    card = client.get("/inbox").text
    assert 'name="student"' not in card and 'name="paper_date"' not in card
    client.post(f"/inbox/{doc.id}/approve", data={"domain": "education", "category": "diploma",
                                                  **_guess_form(DIPLOMA)})
    session.expire_all()
    filed = session.get(Document, doc.id)
    pedro = subjects.find(session, SubjectKind.PERSON, "Pedro")
    rute = subjects.find(session, SubjectKind.PERSON, "Rute")
    assert filed.filename == "2002-12-06 · Diploma or certificate · Diploma Gestao TIC Catolica · Pedro.pdf"
    assert json.loads(filed.fields_json)["student"] == "Pedro" and filed.who_subject_id == pedro.id
    assert doc.id in who.document_ids(session, pedro.id)
    client.cookies.set(who.COOKIE, str(pedro.id))
    mine = client.get("/education").text
    assert f"/education/documents/{doc.id}" in mine and "Showing what is about <b>Pedro</b>" in mine
    assert "Diploma Gestao TIC Catolica · Pedro" in client.get("/papers").text
    client.cookies.set(who.COOKIE, str(rute.id))
    assert f"/education/documents/{doc.id}" not in client.get("/education").text
    client.cookies.clear()
    assert f"/education/documents/{doc.id}" in client.get("/education").text


@pytest.mark.asyncio
async def test_a_field_the_guess_does_not_answer_is_still_asked(client, session, gateway):
    await _waiting(session, {**AEG, "items": [], "paper_date": None}, Category.HOUSE, "warranty_invoice", "x.pdf")
    card = client.get("/inbox").text
    assert 'name="item_name"' in card and 'name="purchase_date"' in card


@pytest.mark.asyncio
async def test_the_file_is_served_as_received_under_the_papers_name(client, session, gateway):
    doc = await _waiting(session, AEG, Category.HOUSE, "warranty_invoice", "AEG Fatura.pdf")
    client.post(f"/inbox/{doc.id}/approve", data={"domain": "house", "category": "warranty_invoice",
                                                  **_guess_form(AEG)})
    session.expire_all()
    filed = session.get(Document, doc.id)
    response = client.get(f"/documents/{doc.id}/file")
    assert response.status_code == 200 and response.content == open(filed.file_path, "rb").read()
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("inline") and "Fatura%20AEG%20eletrodomesticos%20cozinha.pdf" in disposition
    missing = client.get("/documents/999999/file")
    assert missing.status_code == 404 and "not on the server" in missing.text


def test_renaming_keeps_the_first_name_and_the_files_ending():
    doc = Document(filename="Scan 01.PDF", file_path="/x/abc.pdf", content_hash="h", source=DocumentSource.EMAIL)
    assert paper_filing.rename(doc, "2026-01-02 · Bill · EDP") and doc.filename == "2026-01-02 · Bill · EDP.pdf"
    assert paper_filing.rename(doc, "2026-01-02 · Bill · EDP luz")
    assert doc.original_filename == "Scan 01.PDF" and doc.filename == "2026-01-02 · Bill · EDP luz.pdf"
    assert not paper_filing.rename(doc, "2026-01-02 · Bill · EDP luz") and not paper_filing.rename(doc, "  ")


@pytest.mark.asyncio
async def test_approving_again_never_doubles_the_items(session, gateway):
    doc = await _waiting(session, AEG, Category.HOUSE, "warranty_invoice", "AEG Fatura.pdf")
    paper_filing.file_under(session, doc, {**AEG, "filed_name": AEG_NAME})
    paper_filing.file_under(session, doc, {**AEG, "filed_name": AEG_NAME})
    session.commit()
    assert len(paper_filing.items_of(session, doc.id)) == 3


@pytest.mark.asyncio
async def test_search_finds_the_paper_by_its_new_name_its_old_name_and_its_tags(session, client, gateway):
    from app.services.paper_search import search_papers
    doc = await _waiting(session, AEG, Category.HOUSE, "warranty_invoice", "Scan-7731.pdf")
    client.post(f"/inbox/{doc.id}/approve", data={"domain": "house", "category": "warranty_invoice",
                                                  **_guess_form(AEG)})
    session.expire_all()
    for words in ("eletrodomesticos", "Scan-7731", "appliances"):
        assert doc.id in {h.document.id for h in search_papers(session, words)}, words
