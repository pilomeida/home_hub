"""PLAN step 12a: the foundation. Areas are an open list (the nine built in, plus any Pedro approves), a kind can be
added to any area from an approval, "Other" is retired, and the family and the rooms are defined once as subjects.
Done when: a paper that fits nothing is filed in the Inbox under a new area or a new kind created by that approval,
read by the generic reader, shown on its area's page and offered on the Privacy page; the family and room lists are
seeded once; a room not on the list joins it when an item is filed there."""

import json

import pytest
from sqlmodel import select

from app.categories import registry
from app.categories.fields import load_fields
from app.categories.registry import get_spec, implemented_categories, is_implemented
from app.llm_gateway import set_gateway_provider
from app.models.category import Category
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.paper_type import PaperType
from app.models.subject import Subject, SubjectKind
from app.privacy.settings import known_types
from app.services import household, inbox_service, paper_types
from app.services.ingestion import IncomingFile
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.fakes.text_pdf import text_pdf_bytes

CONTRACT = ["CONTRATO DE TRABALHO", "Entre Empresa Exemplo, Lda e Pedro Almeida", "Lisboa, 02/01/2026",
            "Funcao: gestor de projeto"]
READING = {"title": "Empresa Exemplo - contrato de trabalho", "date": "2026-01-02", "about": "Pedro Almeida",
           "summary": "Contrato de trabalho entre a Empresa Exemplo e Pedro Almeida, desde 02/01/2026."}


@pytest.fixture()
def gateway():
    fake = FakeGateway()
    set_gateway_provider(lambda: fake)
    yield fake
    set_gateway_provider(None)


async def _waiting(session, name="scan_7.pdf"):
    async def classify(file_path, context_text):
        from app.services.category_classifier import CategorySuggestion
        return CategorySuggestion(None, None, 0.0, "fits none")
    receipt = await inbox_service.receive_document(
        session, IncomingFile(name, text_pdf_bytes(CONTRACT), DocumentSource.TELEGRAM),
        context_text="Contrato de trabalho - Pedro", external_ref=None, classify=classify)
    return receipt.document


# --- areas are an open list -------------------------------------------------------------------------------------

def test_the_built_in_areas_keep_their_values_and_other_is_gone():
    assert Category("financials") is Category.FINANCIALS or Category("financials") == Category.FINANCIALS
    assert Category.HOUSE.value == "house" and Category.HOUSE.name == "HOUSE"
    values = {spec.category.value for spec in implemented_categories()}
    assert values == {"financials", "house", "health", "insurance", "identity", "vehicles", "education", "pets"}
    assert not hasattr(Category, "OTHER")
    assert not any(getattr(spec, "catch_all", False) for spec in implemented_categories())


def test_an_unknown_area_is_refused_until_it_is_added(session):
    with pytest.raises(ValueError):
        Category("work")
    paper_types.add_area(session, "Work", "Papers about a job: contracts, payslips.", first_kind="Employment contract",
                         by="pedro@example.com")
    work = Category("work")
    assert is_implemented(work) and get_spec(work).label == "Work"
    assert [k.label for k in get_spec(work).kinds] == ["Employment contract"]


def test_an_added_area_is_stored_like_the_built_in_ones(session):
    paper_types.add_area(session, "Work", "", first_kind="Payslip", by=None)
    doc = Document(filename="a.pdf", file_path="/x/a.pdf", content_hash="h", source=DocumentSource.MANUAL,
                   category=Category("work"), paper_kind="payslip")
    session.add(doc)
    session.commit()
    stored = session.connection().exec_driver_sql("select domain from documents").scalar()
    assert stored == "WORK"                                         # names, like FINANCIALS: no paper changes
    session.expire_all()
    assert session.get(Document, doc.id).category == Category("work")


def test_a_kind_added_to_a_built_in_area_is_read_by_the_generic_reader(session):
    paper_types.add_kind(session, Category.FINANCIALS, "Contract", "A signed contract.", by=None)
    spec = get_spec(Category.FINANCIALS)
    kind = spec.kind("contract")
    assert kind.label == "Contract" and kind.reader is not None
    assert {f.key for f in spec.fields_for("contract")} >= {"title", "paper_date", "about", "summary"}
    assert ("financials", "contract") in {(i.category, i.paper_kind) for i in known_types()}   # on the Privacy page


def test_names_are_unique_and_plain(session):
    paper_types.add_area(session, "Work & Jobs", "", first_kind="Contrato de adesão", by=None)
    assert is_implemented(Category("work_jobs"))
    assert get_spec(Category("work_jobs")).kinds[0].value == "contrato_de_adesao"
    with pytest.raises(paper_types.AlreadyThere):
        paper_types.add_area(session, "work jobs", "", first_kind="X", by=None)
    with pytest.raises(paper_types.AlreadyThere):
        paper_types.add_area(session, "House", "", first_kind="X", by=None)
    with pytest.raises(paper_types.AlreadyThere):
        paper_types.add_kind(session, Category.HOUSE, "Maintenance log", "", by=None)   # house has it already


def test_another_process_sees_a_new_type_without_a_restart(session, engine):
    from sqlmodel import Session
    registry.implemented_categories()                               # this process has read the list
    with Session(engine) as other:                                  # the Telegram bot adds one
        other.add(PaperType(area="pets_more", kind=None, label="Pets more", description=""))
        other.add(PaperType(area="pets_more", kind="note", label="Note", description=""))
        other.commit()
    registry.forget_types()                                         # what the timed re-check does
    assert is_implemented(Category("pets_more"))


# --- the Inbox files a paper under a new area or kind ------------------------------------------------------------

@pytest.mark.asyncio
async def test_done_when_a_paper_is_filed_under_a_new_area_made_by_its_approval(session, inbox_documents_dir, gateway):
    document = await _waiting(session)
    gateway._bodies.append(gateway_text_result(json.dumps(READING)))
    filed = await inbox_service.approve(
        session, document.id, category_value=paper_types.NEW, paper_kind=paper_types.NEW, fields={},
        new_area="Work", new_kind="Employment contract", reviewed_by="pedro@example.com")
    assert filed.status == DocumentStatus.PROCESSED
    assert filed.category == Category("work") and filed.paper_kind == "employment_contract"
    assert load_fields(filed)["title"] == READING["title"]
    row = session.exec(select(PaperType).where(PaperType.area == "work", PaperType.kind.is_(None))).one()
    assert row.created_from_document_id == filed.id and row.created_by == "pedro@example.com"


@pytest.mark.asyncio
async def test_a_paper_is_filed_under_a_new_kind_of_a_built_in_area(session, inbox_documents_dir, gateway):
    document = await _waiting(session)
    gateway._bodies.append(gateway_text_result(json.dumps(READING)))
    filed = await inbox_service.approve(
        session, document.id, category_value="financials", paper_kind=paper_types.NEW, fields={},
        new_kind="Contract", reviewed_by="pedro@example.com")
    assert filed.category == Category.FINANCIALS and filed.paper_kind == "contract"
    assert filed.status == DocumentStatus.PROCESSED


@pytest.mark.asyncio
async def test_a_new_area_without_a_name_says_what_to_do(session, inbox_documents_dir, gateway):
    document = await _waiting(session)
    with pytest.raises(inbox_service.InvalidClassification) as exc:
        await inbox_service.approve(session, document.id, category_value=paper_types.NEW, paper_kind=paper_types.NEW,
                                    fields={}, new_area=" ", new_kind="", reviewed_by=None)
    assert "name" in exc.value.errors["domain"].lower()
    assert session.exec(select(PaperType)).first() is None          # a refused approval adds nothing


@pytest.mark.asyncio
async def test_the_inbox_card_offers_a_new_area_and_a_new_kind(client, session, inbox_documents_dir, gateway):
    document = await _waiting(session)
    page = client.get("/inbox").text
    assert "+ New area" in page
    new_area = client.get(f"/inbox/{document.id}/fields", params={"domain": paper_types.NEW}).text
    assert 'name="new_area"' in new_area and 'name="new_kind"' in new_area
    money = client.get(f"/inbox/{document.id}/fields", params={"domain": "financials"}).text
    assert "+ New kind of paper" in money and 'name="new_kind"' not in money
    new_kind = client.get(f"/inbox/{document.id}/fields",
                          params={"domain": "financials", "category": paper_types.NEW}).text
    assert 'name="new_kind"' in new_kind and 'name="new_area"' not in new_kind
    gateway._bodies.append(gateway_text_result(json.dumps(READING)))
    response = client.post(f"/inbox/{document.id}/approve", data={
        "domain": paper_types.NEW, "category": paper_types.NEW, "new_area": "Work", "new_kind": "Employment contract"})
    assert response.status_code in (200, 303)
    session.expire_all()
    assert session.get(Document, document.id).category == Category("work")


def test_an_added_area_has_its_pages(client, session, engine):
    paper_types.add_area(session, "Work", "Papers about a job.", first_kind="Payslip", by=None)
    doc = Document(filename="2026-01-31 · Payslip · January · Pedro.pdf", file_path="/nowhere.pdf", content_hash="h2",
                   source=DocumentSource.MANUAL, category=Category("work"), paper_kind="payslip",
                   status=DocumentStatus.PROCESSED, fields_json=json.dumps({"title": "January payslip"}))
    session.add(doc)
    session.commit()
    landing = client.get("/areas/work")
    assert landing.status_code == 200 and "January payslip" in landing.text
    assert client.get(f"/areas/work/documents/{doc.id}").status_code == 200
    assert client.get("/areas/nope").status_code == 404


def test_old_other_links_lead_to_all_papers(client):
    response = client.get("/other", follow_redirects=False)
    assert response.status_code in (301, 303, 307, 308) and response.headers["location"] == "/papers"


# --- the family and the rooms, once ------------------------------------------------------------------------------

def test_the_family_and_rooms_are_seeded_once(session):
    first = household.ensure(session)
    assert first == {"people": 4, "rooms": 17}
    assert household.ensure(session) == {"people": 0, "rooms": 0}  # a second run changes nothing
    assert [s.name for s in household.family(session)] == ["Pedro", "Rute", "Matias", "Vicente"]
    rooms = [s.name for s in household.rooms(session)]
    assert rooms[0] == "Kitchen" and "Whole house" in rooms and "Porch" in rooms and len(rooms) == 17


def test_a_known_person_joins_the_family_rather_than_doubling(session):
    from app.services import subjects
    subjects.register_person(session, "Rute")
    session.commit()
    household.ensure(session)
    people = session.exec(select(Subject).where(Subject.kind == SubjectKind.PERSON)).all()
    assert sorted(p.name for p in people) == ["Matias", "Pedro", "Rute", "Vicente"]
    assert all(p.household and p.confirmed for p in people)


def test_the_room_field_suggests_the_room_list(session):
    from app.categories.fields import build_form_fields
    household.ensure(session)
    spec = get_spec(Category.HOUSE)
    room = next(f for f in build_form_fields(session, spec, "warranty_invoice", {}, {}) if f.spec.key == "room")
    labels = [label for _, label in room.options]
    assert "Kitchen" in labels and "Matias' bedroom" in labels


@pytest.mark.asyncio
async def test_an_item_filed_in_a_room_not_on_the_list_adds_it(session, inbox_documents_dir, levels_not_binding):
    from app.services.ingestion import Classification, ingest
    household.ensure(session)
    await ingest(session, IncomingFile("manual.pdf", text_pdf_bytes(["Manual"]), DocumentSource.MANUAL),
                 Classification(category=Category.HOUSE, paper_kind="house_appliance",
                                fields={"item_name": "Lawnmower", "room": "Garden shed"}))
    assert "Garden shed" in [s.name for s in household.rooms(session)]


def test_a_new_type_is_seen_at_once_on_a_machine_that_just_started(session, monkeypatch):
    """The deploy's test machine had been up for minutes: "re-read now" must not depend on the clock (run 45252742638)."""
    monkeypatch.setattr(registry.time, "monotonic", lambda: 5.0)
    registry.implemented_categories()
    paper_types.add_area(session, "Work", "", first_kind="Payslip", by=None)
    assert is_implemented(Category("work"))
