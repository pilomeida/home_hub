"""PLAN step 12c: the Inbox card shows what the Hub read and Pedro edits it before approving. Item rows (edit, remove,
add), "New" on a new area, kind, room or person, tags editable, the name shown; approving creates what is new and
keeps the approved guess on the Inbox item; a refused approval creates nothing.
Done when: the AEG invoice's card shows its items in the Kitchen with a warranty end 3 years after the paper's date;
Pedro removes one row, moves one to a new room and approves; the room exists and the approved guess holds his rows."""

import json

import pytest

from app.llm_gateway import set_gateway_provider
from app.models.category import Category
from app.models.document import DocumentSource, DocumentStatus
from app.models.subject import SubjectKind
from app.services import category_classifier, household, inbox_service, subjects
from app.services.ingestion import IncomingFile
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.fakes.text_pdf import text_pdf_bytes

AEG = {"who": None, "paper_date": "2024-03-14", "what": "Fatura AEG eletrodomesticos cozinha",
       "tags": ["AEG", "appliances"], "act_by": None,
       "items": [{"name": "Exaustor AEG DGE5861HM", "room": "Kitchen", "new_room": False},
                 {"name": "Micro-ondas AEG MSB2547D-M", "room": "Kitchen", "new_room": False},
                 {"name": "Produtos de limpeza AEG", "room": "Kitchen", "new_room": False}]}


@pytest.fixture()
def gateway():
    fake = FakeGateway([gateway_text_result(json.dumps({"expiry_date": None, "purchase_date": "2024-03-14"}))] * 3)
    set_gateway_provider(lambda: fake)
    yield fake
    set_gateway_provider(None)


async def _waiting(session, guess: dict, category=None, kind=None, confidence=0.5, filename="AEG Fatura.pdf"):
    household.ensure(session)

    async def classify(file_path, context_text):
        return category_classifier.CategorySuggestion(category, kind, confidence, "A guess.", dict(guess))
    receipt = await inbox_service.receive_document(
        session, IncomingFile(filename, text_pdf_bytes(["Fatura"]), DocumentSource.EMAIL),
        context_text=None, external_ref=None, classify=classify)
    return receipt.document


# --- what the card shows -------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_aeg_card_shows_its_items_with_rooms_and_warranty_and_the_name(client, session):
    doc = await _waiting(session, AEG, Category.HOUSE, "warranty_invoice")
    page = client.get("/inbox").text
    assert "What the Hub read" in page
    assert page.count('name="g_item_name"') == 3 and 'value="Exaustor AEG DGE5861HM"' in page
    assert page.count('name="g_item_warranty" value="2027-03-14"') == 3            # paper date + 3 years
    assert 'value="Fatura AEG eletrodomesticos cozinha"' in page and 'value="AEG, appliances"' in page
    assert "2024-03-14 · Warranty invoice · Fatura AEG eletrodomesticos cozinha" in page
    assert 'value="house" selected' in page                                           # the guess pre-fills the card
    assert "New room" not in page and "New person" not in page
    row = client.get(f"/inbox/{doc.id}/item-row").text
    assert 'name="g_item_name" value=""' in row and f'list="rooms-{doc.id}"' in row


@pytest.mark.asyncio
async def test_a_new_area_and_kind_are_typed_in_and_marked_new(client, session):
    await _waiting(session, {"new_area": "Work", "new_kind": "Employment contract", "who": "Pedro",
                             "who_is_family": True, "what": "Contrato de trabalho", "items": [], "tags": []})
    page = client.get("/inbox").text
    assert 'name="new_area" value="Work"' in page and 'name="new_kind" value="Employment contract"' in page
    assert 'class="chip new"' in page
    assert "Item</th>" not in page                                                    # no item rows outside House
    assert " · Employment contract · Contrato de trabalho · Pedro" in page


@pytest.mark.asyncio
async def test_a_room_or_person_not_on_the_lists_is_marked_new(client, session):
    guess = {**AEG, "who": "Joana", "items": [{"name": "Lamp", "room": "Garage", "new_room": True}]}
    await _waiting(session, guess, Category.HOUSE, "warranty_invoice")
    page = client.get("/inbox").text
    assert "New room" in page and "New person" in page


@pytest.mark.asyncio
async def test_changing_the_area_keeps_what_pedro_typed(client, session):
    doc = await _waiting(session, AEG, Category.HOUSE, "warranty_invoice")
    redrawn = client.get(f"/inbox/{doc.id}/fields", params={
        "domain": "house", "category": "ownership_document", "g_who": "", "g_paper_date": "2024-03-14", "g_what": "Typed by Pedro",
        "g_tags": "x", "g_item_name": ["Oven"], "g_item_room": ["Kitchen"], "g_item_warranty": [""]}).text
    assert 'value="Typed by Pedro"' in redrawn and 'value="Oven"' in redrawn
    assert "2024-03-14 · Ownership document · Typed by Pedro" in redrawn   # the name follows the new kind


# --- approving -----------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_done_when_pedro_edits_the_rows_and_approves(client, session, gateway):
    doc = await _waiting(session, AEG, Category.HOUSE, "warranty_invoice")
    response = client.post(f"/inbox/{doc.id}/approve", data={
        "domain": "house", "category": "warranty_invoice", "item_name": "AEG kitchen appliances", "room": "Kitchen",
        "g_who": "", "g_paper_date": "2024-03-14", "g_what": "Fatura AEG eletrodomesticos cozinha",
        "g_tags": "AEG, appliances, kitchen",
        "g_item_name": ["Exaustor AEG DGE5861HM", "Micro-ondas AEG MSB2547D-M"],     # cleaning products removed
        "g_item_room": ["Kitchen", "Store room upstairs"],                            # one moved to a new room
        "g_item_warranty": ["2027-03-14", ""]})
    assert response.status_code == 200
    session.expire_all()
    room = subjects.find(session, SubjectKind.ROOM, "Store room upstairs")
    assert room is not None and room.confirmed
    item = inbox_service.item_for(session, doc.id)
    approved = json.loads(item.guess_json)
    assert [(i["name"], i["room"], i["warranty_end"]) for i in approved["items"]] == [
        ("Exaustor AEG DGE5861HM", "Kitchen", "2027-03-14"), ("Micro-ondas AEG MSB2547D-M", "Store room upstairs", "2027-03-14")]
    assert approved["tags"] == ["AEG", "appliances", "kitchen"]
    assert approved["filed_name"] == "2024-03-14 · Warranty invoice · Fatura AEG eletrodomesticos cozinha"
    assert approved["approved_at"] and not any(i["new_room"] for i in approved["items"])


@pytest.mark.asyncio
async def test_a_new_person_is_added_on_approval_and_items_stay_in_house(session, inbox_documents_dir, gateway):
    doc = await _waiting(session, {"new_area": "Work", "new_kind": "Contract", "who": "Joana Silva", "items": [
        {"name": "WI-FAST", "room": None, "new_room": False}], "what": "Contrato", "tags": []})
    gateway._bodies.append(gateway_text_result(json.dumps({"title": "Contrato", "date": None, "about": "Joana",
                                                           "summary": "A contract."})))
    await inbox_service.approve(session, doc.id, category_value="__new__", paper_kind="__new__", new_area="Work",
                                new_kind="Contract", fields={}, reviewed_by="pedro@example.com")
    assert subjects.find(session, SubjectKind.PERSON, "Joana Silva") is not None
    approved = inbox_service.guess_of(inbox_service.item_for(session, doc.id))
    assert approved["items"] == [] and approved["filed_name"].endswith(" · Contract · Contrato · Joana Silva")
    assert "new_area" not in approved and approved["approved_by"] == "pedro@example.com"


@pytest.mark.asyncio
async def test_a_refused_approval_creates_no_room_or_person(client, session):
    doc = await _waiting(session, AEG, Category.HOUSE, "warranty_invoice")
    response = client.post(f"/inbox/{doc.id}/approve", data={
        "domain": "house", "category": "maintenance_log",                             # the service date is missing
        "g_who": "Joana", "g_paper_date": "2024-03-14", "g_what": "Fatura", "g_tags": "",
        "g_item_name": ["Lamp"], "g_item_room": ["Garage"], "g_item_warranty": [""]})
    assert response.status_code == 422
    assert 'value="Garage"' in response.text and 'value="Joana"' in response.text     # his edits come back
    session.expire_all()
    assert subjects.find(session, SubjectKind.ROOM, "Garage") is None
    assert subjects.find(session, SubjectKind.PERSON, "Joana") is None
    assert "approved_at" not in inbox_service.guess_of(inbox_service.item_for(session, doc.id))


@pytest.mark.asyncio
async def test_pedros_edits_are_checked_like_the_ais(client, session, gateway):
    doc = await _waiting(session, AEG, Category.HOUSE, "warranty_invoice")
    client.post(f"/inbox/{doc.id}/approve", data={
        "domain": "house", "category": "warranty_invoice", "item_name": "AEG", "g_who": "pedro",
        "g_paper_date": "not a date", "g_what": "w" * 500, "g_tags": ",".join(f"t{n}" for n in range(30)),
        "g_item_name": [f"Thing {n}" for n in range(30)], "g_item_room": ["Kitchen"] * 30,
        "g_item_warranty": ["soon"] * 30})
    session.expire_all()
    approved = inbox_service.guess_of(inbox_service.item_for(session, doc.id))
    assert approved["who"] == "Pedro" and approved["who_is_family"] is True           # matched to the family
    assert approved["paper_date"] is None and len(approved["what"]) <= 120
    assert len(approved["tags"]) == 10 and len(approved["items"]) == 20
    assert all(i["warranty_end"] == "" for i in approved["items"])                    # no date, no guessed end
    assert session.get(type(doc), doc.id).status != DocumentStatus.PENDING_REVIEW
