import io
import json

import pytest
from sqlmodel import select

from app.categories import registry
from app.categories.fields import InvalidClassification, load_fields
from app.categories.insurance.policies import build_policy_cards, policy_sections
from app.models.document import DocumentSource, DocumentStatus
from app.models.category import Category
from app.models.wiki import WikiPage
from app.services.ingestion import Classification, IncomingFile, ingest, refile_document


@pytest.fixture(autouse=True)
def documents_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "docs")


_n = [0]


async def _ingest(session, category, fields, filename=None):
    _n[0] += 1
    result = await ingest(session, IncomingFile(filename or f"doc{_n[0]}.pdf", f"bytes{_n[0]}".encode(), DocumentSource.MANUAL),
                          Classification(Category.INSURANCE, category, fields))
    return result.document


ZURICH = {"insurer": "Zurich", "policy_number": "009886609", "insured": "Mafra house", "product": "Zurich Lar Seguro",
          "covers": "home"}


def test_the_registry_has_insurance_whose_kinds_are_papers():
    """Work-list B1b (Pedro, 2026-11-04): the kind is what the paper is; what the policy covers is a detail."""
    spec = registry.get_spec(Category.INSURANCE)
    assert spec.home_url == "/insurance"
    assert [c.value for c in spec.kinds] == ["policy", "claim"]
    covers = next(f for f in spec.fields if f.key == "covers")
    assert covers.required and [v for v, _ in covers.choices] == ["home", "car", "health", "life"]


@pytest.mark.asyncio
async def test_a_policy_document_is_stored_tagged_and_gets_a_policy_page(session):
    document = await _ingest(session, "policy", {**ZURICH, "part": "general_conditions"})
    assert document.status == DocumentStatus.PROCESSED and document.category == Category.INSURANCE
    page = session.exec(select(WikiPage).where(WikiPage.page_type == "insurance.policy")).one()
    assert page.topic == "009886609" and page.category == Category.INSURANCE
    from app.models.wiki import WikiClaim
    types = [c.value for c in session.exec(select(WikiClaim).where(WikiClaim.page_id == page.id, WikiClaim.key == "type")).all()]
    assert types == ["Home insurance"]


@pytest.mark.asyncio
async def test_insurer_policy_number_what_it_covers_and_the_part_are_required(session):
    with pytest.raises(InvalidClassification) as err:
        await _ingest(session, "policy", {"product": "Liber 3G"})
    assert set(err.value.errors) == {"insurer", "policy_number", "covers", "part"}
    with pytest.raises(InvalidClassification) as err:
        await _ingest(session, "claim", {"product": "Liber 3G"})
    assert set(err.value.errors) == {"insurer", "policy_number", "covers"}      # a claim is no part of the policy


@pytest.mark.asyncio
async def test_documents_of_one_policy_share_a_card_in_reading_order(session):
    await _ingest(session, "claim", {**ZURICH}, "claim.pdf")
    await _ingest(session, "policy", {**ZURICH, "part": "payment_plan"}, "plan.pdf")
    await _ingest(session, "policy", {**ZURICH, "part": "general_conditions"}, "conditions.pdf")
    await _ingest(session, "policy", {**ZURICH, "part": "policy_schedule"}, "schedule.pdf")
    await _ingest(session, "policy", {"insurer": "Fidelidade", "policy_number": "756917295", "covers": "car",
                                      "part": "green_card", "insured": "Toyota Avensis"}, "carta.pdf")
    cards = build_policy_cards(session)
    assert len(cards) == 2
    zurich = next(c for c in cards if c.insurer == "Zurich")
    assert [p.entry.title for p in zurich.documents] == ["schedule.pdf", "conditions.pdf", "plan.pdf", "claim.pdf"]
    assert [p.note for p in zurich.documents][-1] == "claim"
    assert zurich.insured == "Mafra house" and zurich.product == "Zurich Lar Seguro"
    sections = {title: [c.insurer for c in cs] for title, cs in policy_sections(session)}
    assert sections == {"Home": ["Zurich"], "Cars": ["Fidelidade"], "Health": [], "Life": []}


@pytest.mark.asyncio
async def test_a_house_document_can_be_refiled_to_insurance(session):
    from app.models.document import DocumentSource as S
    result = await ingest(session, IncomingFile("manual.pdf", b"x", S.MANUAL),
                          Classification(Category.HOUSE, "ownership_document", {}))
    moved = await refile_document(session, result.document, Classification(
        Category.INSURANCE, "policy", {**ZURICH, "part": "policy_schedule"}))
    assert moved.category == Category.INSURANCE and load_fields(moved)["policy_number"] == "009886609"


# --- router -------------------------------------------------------------------

def _upload(client, data, filename="p.pdf"):
    content = f"pdf {filename}".encode()          # one file per name: the same bytes twice is a duplicate
    return client.post("/insurance/upload", data=data, files={"file": (filename, io.BytesIO(content), "application/pdf")},
                       follow_redirects=False)


def test_upload_form_offers_the_papers_and_asks_what_the_policy_covers(client):
    page = client.get("/insurance/upload")
    assert page.status_code == 200 and "What kind of paper" in page.text
    assert "Policy" in page.text and "Claim" in page.text and "Premium notice or receipt (filed with the Bills)" in page.text
    fields = client.get("/insurance/upload/fields?category=policy").text
    assert "policy_number" in fields and "covers" in fields and "Which part of the policy" in fields
    premium = client.get("/insurance/upload/fields?category=premium_notice").text
    assert "policy_number" in premium and "covers" not in premium        # a bill's fields


def test_upload_shows_on_the_landing_with_a_detail_page_and_edit(client, session):
    data = {"category": "policy", "insurer": "Real Vida Seguros", "policy_number": "91/038975", "covers": "health",
            "part": "policy_schedule", "insured": "Pedro, Rute, Matias, Vicente"}
    assert _upload(client, data, "condicoes.pdf").status_code == 303
    landing = client.get("/insurance").text
    assert "Health" in landing and "Real Vida Seguros" in landing and "condicoes.pdf" in landing and "91/038975" in landing
    from app.models.document import Document
    document = session.exec(select(Document).where(Document.category == Category.INSURANCE)).one()
    detail = client.get(f"/insurance/documents/{document.id}")
    assert detail.status_code == 200 and "Pedro, Rute, Matias, Vicente" in detail.text
    edited = client.post(f"/insurance/documents/{document.id}/fields", data={**{k: v for k, v in data.items() if k != "category"},
                                                                       "notes": "396.29 EUR a year"}, follow_redirects=False)
    assert edited.status_code == 303
    session.expire_all()
    assert json.loads(session.get(Document, document.id).fields_json)["notes"] == "396.29 EUR a year"


def test_missing_fields_or_file_are_rejected_and_other_domains_404(client, session):
    assert _upload(client, {"category": "policy", "insurer": "X"}).status_code == 400
    assert client.post("/insurance/upload", data={"category": "policy"}).status_code == 400
    assert _upload(client, {"category": "premium_notice"}).status_code == 400       # which policy it pays
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
                   category=Category.HOUSE, paper_kind="insurance_policy", status=DocumentStatus.PROCESSED,
                   fields_json=json.dumps({"insurer": "Zurich", "policy_number": "009886609", "document_kind": "policy_schedule",
                                           "property": "Mafra house", "insurance_notes": "n", "broker": "EXS"}))
    other = Document(filename="d.pdf", file_path="/tmp/d.pdf", content_hash="h-d", source=DocumentSource.MANUAL,
                     category=Category.HOUSE, paper_kind="ownership_document", status=DocumentStatus.PROCESSED, fields_json="{}")
    session.add(old); session.add(other); session.commit()
    assert move_house_insurance_documents(session) == 1
    session.expire_all()
    moved = session.get(Document, old.id)
    assert (moved.category, moved.paper_kind) == (Category.INSURANCE, "home_insurance")
    assert json.loads(moved.fields_json) == {"insurer": "Zurich", "policy_number": "009886609", "document_kind": "policy_schedule",
                                             "insured": "Mafra house", "notes": "n", "broker": "EXS"}
    assert (session.get(Document, other.id).category, session.get(Document, other.id).paper_kind) == (Category.HOUSE, "ownership_document")
    assert move_house_insurance_documents(session) == 0  # idempotent


# --- premium notices are bills (Pedro, 2026-11-04) -------------------------------------------------------------

def test_a_premium_notice_uploaded_in_insurance_is_a_bill_shown_on_its_policy(client, session, monkeypatch):
    """Filed once, under Bills, with its policy number; listed on the policy's card; not an Insurance paper."""
    import app.categories.financials.handler as financials_handler
    from app.models.document import Document

    async def read_as_bill(session, document):           # the bill reader, without the model
        document.status = DocumentStatus.PROCESSED
        session.add(document); session.commit(); session.refresh(document)
        return document

    monkeypatch.setattr(financials_handler, "process_financials_document", read_as_bill)
    assert _upload(client, {"category": "policy", "insurer": "MetLife", "policy_number": "PT-123", "covers": "life",
                            "part": "policy_schedule"}, "apolice.pdf").status_code == 303
    response = _upload(client, {"category": "premium_notice", "policy_number": "pt-123"}, "aviso.pdf")
    assert response.status_code == 303 and response.headers["location"].startswith("/financials/bills/")
    bill = session.exec(select(Document).where(Document.filename == "aviso.pdf")).one()
    assert (bill.category, bill.paper_kind) == (Category.FINANCIALS, "bill")
    assert load_fields(bill)["policy_number"] == "pt-123"
    card = build_policy_cards(session)[0]
    assert [(p.entry.title, p.note) for p in card.documents] == [
        ("apolice.pdf", "policy schedule (particular conditions)"), ("aviso.pdf", "premium notice (in Bills)")]
    assert "aviso.pdf" in client.get("/insurance").text


def test_the_migration_turns_the_old_kinds_into_papers():
    import importlib.util
    spec = importlib.util.spec_from_file_location("m", "alembic/versions/d2b6f8a1c437_insurance_kinds_are_papers.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    assert m._moved({"insurer": "Zurich", "document_kind": "general_conditions"}, "home_insurance") == (
        "policy", {"insurer": "Zurich", "covers": "home", "part": "general_conditions"})
    assert m._moved({"document_kind": "claim"}, "car_insurance") == ("claim", {"covers": "car"})
    assert m._moved({}, "life_insurance") == ("policy", {"covers": "life", "part": "other"})


@pytest.mark.asyncio
async def test_the_move_script_files_the_old_premium_notices_as_bills(session, monkeypatch):
    """The six premium notices of 2026-11-04 go through the re-file path: off the policy page, onto Bills."""
    import importlib.util
    import app.categories.financials.handler as financials_handler
    from app.models.document import Document

    async def read_as_bill(session, document):
        document.status = DocumentStatus.PROCESSED
        session.add(document); session.commit(); session.refresh(document)
        return document

    monkeypatch.setattr(financials_handler, "process_financials_document", read_as_bill)
    policy = await _ingest(session, "policy", {**ZURICH, "part": "policy_schedule"}, "schedule.pdf")
    old = await _ingest(session, "policy", {**ZURICH, "part": "other"}, "aviso.pdf")
    old.paper_kind = "home_insurance"                                         # as the migration leaves it
    old.fields_json = json.dumps({**{k: v for k, v in ZURICH.items() if k != "covers"}, "document_kind": "premium_notice"})
    session.add(old); session.commit()

    spec = importlib.util.spec_from_file_location("move", "scripts/move_premium_notices_to_bills.py")
    move = importlib.util.module_from_spec(spec); spec.loader.exec_module(move)
    dry = await move.run(session, apply=False)
    assert dry == [f"document {old.id} aviso.pdf: policy 009886609 -> Bills"]
    await move.run(session, apply=True)
    session.expire_all()
    moved = session.get(Document, old.id)
    assert (moved.category, moved.paper_kind, load_fields(moved)) == (Category.FINANCIALS, "bill", {"policy_number": "009886609"})
    assert session.get(Document, policy.id).category == Category.INSURANCE
    assert [p.note for p in build_policy_cards(session)[0].documents][-1] == "premium notice (in Bills)"
