import io
import json

import pytest
from sqlmodel import select

from app.domains import registry
from app.domains.fields import InvalidClassification, load_fields
from app.domains.insurance.policies import build_policy_cards, policy_sections
from app.models.document import DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.wiki import WikiPage
from app.services.ingestion import Classification, IncomingFile, ingest, refile_document


@pytest.fixture(autouse=True)
def documents_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "docs")


_n = [0]


async def _ingest(session, category, fields, filename=None):
    _n[0] += 1
    result = await ingest(session, IncomingFile(filename or f"doc{_n[0]}.pdf", f"bytes{_n[0]}".encode(), DocumentSource.MANUAL),
                          Classification(Domain.INSURANCE, category, fields))
    return result.document


ZURICH = {"insurer": "Zurich", "policy_number": "009886609", "insured": "Mafra house", "product": "Zurich Lar Seguro"}


def test_the_registry_has_insurance_with_its_four_covers():
    spec = registry.get_spec(Domain.INSURANCE)
    assert spec.home_url == "/insurance"
    assert [c.value for c in spec.categories] == ["home_insurance", "car_insurance", "health_insurance", "life_insurance"]


@pytest.mark.asyncio
async def test_a_policy_document_is_stored_tagged_and_gets_a_policy_page(session):
    document = await _ingest(session, "home_insurance", {**ZURICH, "document_kind": "general_conditions"})
    assert document.status == DocumentStatus.PROCESSED and document.domain == Domain.INSURANCE
    page = session.exec(select(WikiPage).where(WikiPage.page_type == "insurance.policy")).one()
    assert page.topic == "009886609" and page.domain == Domain.INSURANCE


@pytest.mark.asyncio
async def test_insurer_policy_number_and_kind_are_required(session):
    with pytest.raises(InvalidClassification) as err:
        await _ingest(session, "car_insurance", {"product": "Liber 3G"})
    assert set(err.value.errors) == {"insurer", "policy_number", "document_kind"}


@pytest.mark.asyncio
async def test_documents_of_one_policy_share_a_card_in_reading_order(session):
    await _ingest(session, "home_insurance", {**ZURICH, "document_kind": "payment_plan"}, "plan.pdf")
    await _ingest(session, "home_insurance", {**ZURICH, "document_kind": "general_conditions"}, "conditions.pdf")
    await _ingest(session, "home_insurance", {**ZURICH, "document_kind": "policy_schedule"}, "schedule.pdf")
    await _ingest(session, "car_insurance", {"insurer": "Fidelidade", "policy_number": "756917295",
                                             "document_kind": "premium_notice", "insured": "Toyota Avensis"}, "aviso.pdf")
    cards = build_policy_cards(session)
    assert len(cards) == 2
    zurich = next(c for c in cards if c.insurer == "Zurich")
    assert [d.title for d in zurich.documents] == ["schedule.pdf", "conditions.pdf", "plan.pdf"]
    assert zurich.insured == "Mafra house" and zurich.product == "Zurich Lar Seguro"
    sections = {title: [c.insurer for c in cs] for title, cs in policy_sections(session)}
    assert sections == {"Home": ["Zurich"], "Cars": ["Fidelidade"], "Health": [], "Life": []}


@pytest.mark.asyncio
async def test_a_house_document_can_be_refiled_to_insurance(session):
    from app.models.document import DocumentSource as S
    result = await ingest(session, IncomingFile("manual.pdf", b"x", S.MANUAL),
                          Classification(Domain.HOUSE, "ownership_document", {}))
    moved = await refile_document(session, result.document, Classification(
        Domain.INSURANCE, "home_insurance", {**ZURICH, "document_kind": "policy_schedule"}))
    assert moved.domain == Domain.INSURANCE and load_fields(moved)["policy_number"] == "009886609"


# --- router -------------------------------------------------------------------

def _upload(client, data, filename="p.pdf"):
    return client.post("/insurance/upload", data=data, files={"file": (filename, io.BytesIO(b"pdf"), "application/pdf")},
                       follow_redirects=False)


def test_upload_form_lists_the_covers(client):
    page = client.get("/insurance/upload")
    assert page.status_code == 200 and "Car insurance" in page.text and "Health insurance" in page.text
    assert "policy_number" in client.get("/insurance/upload/fields?category=car_insurance").text


def test_upload_shows_on_the_landing_with_a_detail_page_and_edit(client, session):
    data = {"category": "health_insurance", "insurer": "Real Vida Seguros", "policy_number": "91/038975",
            "document_kind": "policy_schedule", "insured": "Pedro, Rute, Matias, Vicente"}
    assert _upload(client, data, "condicoes.pdf").status_code == 303
    landing = client.get("/insurance").text
    assert "Health" in landing and "Real Vida Seguros" in landing and "condicoes.pdf" in landing and "91/038975" in landing
    from app.models.document import Document
    document = session.exec(select(Document).where(Document.domain == Domain.INSURANCE)).one()
    detail = client.get(f"/insurance/documents/{document.id}")
    assert detail.status_code == 200 and "Pedro, Rute, Matias, Vicente" in detail.text
    edited = client.post(f"/insurance/documents/{document.id}/fields", data={**{k: v for k, v in data.items() if k != "category"},
                                                                       "notes": "396.29 EUR a year"}, follow_redirects=False)
    assert edited.status_code == 303
    session.expire_all()
    assert json.loads(session.get(Document, document.id).fields_json)["notes"] == "396.29 EUR a year"


def test_missing_fields_or_file_are_rejected_and_other_domains_404(client, session):
    assert _upload(client, {"category": "car_insurance", "insurer": "X"}).status_code == 400
    assert client.post("/insurance/upload", data={"category": "car_insurance"}).status_code == 400
    assert client.get("/insurance/documents/99999").status_code == 404


def test_the_landing_is_empty_but_complete_with_no_policies(client):
    page = client.get("/insurance").text
    assert all(f"<h2>{t}</h2>" in page for t in ("Home", "Cars", "Health", "Life"))


def test_the_overview_shows_the_insurance_card_and_the_nav_link(client):
    page = client.get("/").text
    assert "/insurance" in page


def test_house_insurance_documents_move_to_the_insurance_domain_with_renamed_fields(session):
    from app.models.document import Document
    from app.services.insurance_move import move_house_insurance_documents
    old = Document(filename="z.pdf", file_path="/tmp/z.pdf", content_hash="h-z", source=DocumentSource.MANUAL,
                   domain=Domain.HOUSE, category="insurance_policy", status=DocumentStatus.PROCESSED,
                   fields_json=json.dumps({"insurer": "Zurich", "policy_number": "009886609", "document_kind": "policy_schedule",
                                           "property": "Mafra house", "insurance_notes": "n", "broker": "EXS"}))
    other = Document(filename="d.pdf", file_path="/tmp/d.pdf", content_hash="h-d", source=DocumentSource.MANUAL,
                     domain=Domain.HOUSE, category="ownership_document", status=DocumentStatus.PROCESSED, fields_json="{}")
    session.add(old); session.add(other); session.commit()
    assert move_house_insurance_documents(session) == 1
    session.expire_all()
    moved = session.get(Document, old.id)
    assert (moved.domain, moved.category) == (Domain.INSURANCE, "home_insurance")
    assert json.loads(moved.fields_json) == {"insurer": "Zurich", "policy_number": "009886609", "document_kind": "policy_schedule",
                                             "insured": "Mafra house", "notes": "n", "broker": "EXS"}
    assert (session.get(Document, other.id).domain, session.get(Document, other.id).category) == (Domain.HOUSE, "ownership_document")
    assert move_house_insurance_documents(session) == 0  # idempotent
