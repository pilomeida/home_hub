"""PLAN step 8d and 8e: Education (school papers per child, fees, diplomas; dates and fees into Coming up) and Pets (a
pet is a thing with a type; vet visits and vaccinations remind; its insurance shows on its card)."""

import json
from datetime import date, datetime, timedelta

import pytest
from sqlmodel import select

from app.categories.fields import load_fields
from app.llm_gateway import set_gateway_provider
from app.models.category import Category
from app.models.document import DocumentSource
from app.models.subject import Subject, SubjectKind
from app.models.todo import Todo
from app.services import coming_dates, ingestion, notices
from app.services.ingestion import Classification, IncomingFile
from app.ui import shell
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.fakes.text_pdf import text_pdf_bytes
from tests.knowledge_factories import make_document

DUE = date.today() + timedelta(days=30)


@pytest.fixture()
def gateway():
    fake = FakeGateway()
    set_gateway_provider(lambda: fake)
    yield fake
    set_gateway_provider(None)


async def _file(session, gateway, category, kind, reading, typed=None):
    gateway._bodies.append(gateway_text_result(json.dumps(reading)))
    return (await ingestion.ingest(session, IncomingFile(f"{kind}.pdf", text_pdf_bytes([kind, "paper"]),
                                                         DocumentSource.MANUAL),
                                   Classification(category, kind, typed or {}))).document


def test_education_under_papers_and_pets_under_things():
    sections = {s.key: s for s in shell.sections()}
    assert any(l.url == "/education" for l in sections["papers"].links)
    assert any(l.url == "/pets" for l in sections["things"].links)
    assert shell.active("/pets/upload").key == "things" and shell.active("/education").key == "papers"


@pytest.mark.asyncio
async def test_a_school_fee_is_a_todo_in_coming_up_and_tells_the_phone_once(session, inbox_documents_dir, gateway):
    fee = await _file(session, gateway, Category.EDUCATION, "school_fee",
                      {"student": "Vicente Almeida", "school": "Colégio X", "school_year": "2026/2027",
                       "paper_date": "2026-11-01", "what": None, "date_to_note": None, "amount": "350.00",
                       "due_on": DUE.isoformat()})
    assert fee.failure_reason is None
    assert session.exec(select(Subject).where(Subject.kind == SubjectKind.PERSON,
                                              Subject.name == "Vicente Almeida")).first() is not None
    [todo] = session.exec(select(Todo).where(Todo.document_id == fee.id)).all()
    assert todo.title == f"Pay the school fee for Vicente €350.00 (due {DUE.isoformat()})"
    assert todo.due_date == DUE - timedelta(days=7)
    rows = [r for m in coming_dates.dates_view(session, date.today()).months for r in m.rows]
    assert [(r.area, r.area_label) for r in rows if "school fee" in r.name] == [("education", "Education")]
    [found] = [f for f in notices.current(session, todo.due_date) if f.kind == notices.PAPER_DATE_DUE]
    assert found.text == f"A school fee for Vicente is due on {DUE.isoformat()}: see To-Dos."
    when = datetime.combine(todo.due_date, datetime.min.time()).replace(hour=10)
    notices.mark_sent(session, notices.refresh(session, when), when)
    assert not [n for n in notices.refresh(session, when + timedelta(days=2)).new if n.kind == notices.PAPER_DATE_DUE]


@pytest.mark.asyncio
async def test_a_school_letter_without_a_date_is_simply_filed(session, inbox_documents_dir, gateway):
    letter = await _file(session, gateway, Category.EDUCATION, "school_paper",
                         {"student": "Vicente Almeida", "school": "Colégio X", "school_year": "", "paper_date": None,
                          "what": "", "date_to_note": None, "amount": "", "due_on": None})
    assert letter.failure_reason is None
    assert session.exec(select(Todo).where(Todo.document_id == letter.id)).all() == []


@pytest.mark.asyncio
async def test_a_vaccination_reminds_and_the_pets_card_shows_its_insurance(client, session, inbox_documents_dir,
                                                                          gateway):
    vax = await _file(session, gateway, Category.PETS, "vaccination",
                      {"pet": "Bolinha", "species": "Dog", "paper_date": "2026-11-01", "vaccine": "Rabies",
                       "next_on": DUE.isoformat()})
    fields = load_fields(vax)
    assert "species" not in fields                                 # "Dog" is not one of the choices: left for a person
    assert session.exec(select(Subject).where(Subject.kind == SubjectKind.THING, Subject.name == "Bolinha")).first()
    [todo] = session.exec(select(Todo).where(Todo.document_id == vax.id)).all()
    assert todo.title == f"Rabies for Bolinha (due {DUE.isoformat()})" and todo.due_date == DUE - timedelta(days=14)
    [found] = [f for f in notices.current(session, todo.due_date) if f.kind == notices.PAPER_DATE_DUE]
    assert found.text == f"A vaccination for Bolinha is due on {DUE.isoformat()}: see To-Dos."
    visit = await _file(session, gateway, Category.PETS, "vet_visit",
                        {"pet": "Bolinha", "species": "dog", "paper_date": "2026-10-01", "vaccine": "",
                         "next_on": None})
    assert visit.failure_reason is None                            # a visit need not say when the next one is
    make_document(session, category=Category.INSURANCE, paper_kind="policy",
                  fields={"insurer": "Allianz", "policy_number": "PET-9", "covers": "pet", "part": "policy_schedule",
                          "insured": "Bolinha"})
    page = client.get("/pets").text
    assert "Bolinha" in page and "Dog" in page and "Allianz PET-9" in page and f"/pets/documents/{vax.id}" in page
    assert client.get("/insurance").status_code == 200 and "Pets" in client.get("/insurance").text


@pytest.mark.asyncio
async def test_the_education_page_groups_by_person(client, session, inbox_documents_dir, gateway):
    await _file(session, gateway, Category.EDUCATION, "diploma",
                {"student": "Rute Correia", "school": "Universidade de Lisboa", "school_year": "",
                 "paper_date": "2010-07-15", "what": "", "date_to_note": None, "amount": "", "due_on": None})
    page = client.get("/education").text
    assert "Rute Correia" in page and "Diploma or certificate" in page and "Universidade de Lisboa" in page
    assert client.get("/education/upload").status_code == 200
