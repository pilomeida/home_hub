"""PLAN step 8c: Vehicles. The car is a subject; inspection, road tax and service papers each make a reminder To-Do
that reaches the phone once; the car's card shows its papers, next dates and the insurance that names it."""

import json
from datetime import date, datetime, timedelta

import pytest
from sqlmodel import select

from app.categories.fields import load_fields
from app.categories.vehicles.handler import plate_key
from app.llm_gateway import set_gateway_provider
from app.models.category import Category
from app.models.document import DocumentSource, DocumentStatus
from app.models.subject import Subject, SubjectKind
from app.models.todo import Todo
from app.services import ingestion, notices
from app.services.ingestion import Classification, IncomingFile
from app.ui import shell
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.fakes.text_pdf import text_pdf_bytes
from tests.knowledge_factories import make_document

NEXT = date.today() + timedelta(days=200)
IPO = ["CENTRO DE INSPECOES", "Matricula 82 CL 65  Toyota Avensis", f"Proxima inspecao ate {NEXT.strftime('%d/%m/%Y')}"]
READING = {"plate": "82 cl 65", "vehicle": "Toyota Avensis", "paper_date": "2026-05-10",
           "next_inspection_on": NEXT.isoformat(), "road_tax_due_on": None, "next_service_on": None,
           "mileage": "182 400 km"}


@pytest.fixture()
def gateway():
    fake = FakeGateway()
    set_gateway_provider(lambda: fake)
    yield fake
    set_gateway_provider(None)


async def _paper(session, gateway, kind="inspection", reading=READING, lines=IPO):
    gateway._bodies.append(gateway_text_result(json.dumps(reading)))
    return (await ingestion.ingest(session, IncomingFile(f"{kind}.pdf", text_pdf_bytes(lines), DocumentSource.MANUAL),
                                   Classification(Category.VEHICLES, kind, {}))).document


def test_plates_are_written_one_way_and_vehicles_sit_under_things():
    assert plate_key("82 cl 65") == plate_key("82CL65") == plate_key("82-CL-65") == "82-CL-65"
    things = next(s for s in shell.sections() if s.key == "things")
    assert any(l.url == "/vehicles" for l in things.links) and shell.active("/vehicles").key == "things"


@pytest.mark.asyncio
async def test_an_inspection_is_read_and_its_reminder_reaches_the_phone_once(session, inbox_documents_dir, gateway):
    doc = await _paper(session, gateway)
    assert doc.status == DocumentStatus.PROCESSED and doc.failure_reason is None
    fields = load_fields(doc)
    assert (fields["plate"], fields["mileage"], fields["next_inspection_on"]) == ("82-CL-65", "182400", NEXT.isoformat())
    assert session.exec(select(Subject).where(Subject.kind == SubjectKind.THING, Subject.name == "82-CL-65")).first()
    [todo] = session.exec(select(Todo).where(Todo.document_id == doc.id)).all()
    assert todo.due_date == NEXT - timedelta(days=30) and todo.category == Category.VEHICLES
    assert todo.title == f"Book the inspection (IPO) for Toyota Avensis 82-CL-65 (due {NEXT.isoformat()})"
    [found] = [f for f in notices.current(session, todo.due_date) if f.kind == notices.PAPER_DATE_DUE]
    assert found.text == f"The inspection (IPO) for Toyota Avensis 82-CL-65 is due on {NEXT.isoformat()}: see To-Dos."
    when = datetime.combine(todo.due_date, datetime.min.time()).replace(hour=10)
    notices.mark_sent(session, notices.refresh(session, when), when)
    again = notices.refresh(session, when + timedelta(days=3))
    assert not [n for n in again.new + again.reminders if n.kind == notices.PAPER_DATE_DUE]


@pytest.mark.asyncio
async def test_a_paid_road_tax_reminds_next_years_and_a_missing_date_says_so(session, inbox_documents_dir, gateway):
    paid = date.today() - timedelta(days=20)
    tax = await _paper(session, gateway, kind="road_tax", lines=["IUC 2026 pago", "82-CL-65"],
                       reading={**READING, "next_inspection_on": None, "road_tax_due_on": paid.isoformat()})
    assert load_fields(tax)["road_tax_due_on"] == paid.replace(year=paid.year + 1).isoformat()
    service = await _paper(session, gateway, kind="service", lines=["Oficina", "82-CL-65"],
                           reading={**READING, "next_inspection_on": None})
    assert "next service date was not found" in service.failure_reason
    assert session.exec(select(Todo).where(Todo.document_id == service.id)).all() == []


@pytest.mark.asyncio
async def test_the_cars_card_shows_papers_dates_and_its_insurance(client, session, inbox_documents_dir, gateway):
    doc = await _paper(session, gateway)
    make_document(session, category=Category.INSURANCE, paper_kind="policy",
                  fields={"insurer": "Fidelidade", "policy_number": "AU-77", "covers": "car", "part": "policy_schedule",
                          "insured": "Toyota Avensis 82CL65"})
    page = client.get("/vehicles").text
    assert "Toyota Avensis 82-CL-65" in page and f"/vehicles/documents/{doc.id}" in page
    assert NEXT.isoformat() in page and "Fidelidade AU-77" in page
    assert client.get(f"/vehicles/documents/{doc.id}").status_code == 200
    from app.categories.vehicles.overview import vehicles_overview_card
    texts = [l.text for l in vehicles_overview_card(session, date.today()).lines]
    assert texts == ["1 car", f"next: inspection for 82-CL-65 on {NEXT.isoformat()}"]
