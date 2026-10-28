"""Health carries no billing: a bill, receipt or reimbursement statement is a bill (Bills / Insurance), not a health paper."""

import json
from datetime import date

import pytest
from sqlmodel import select

from app.domains.health import store
from app.domains.health.ask import HEALTH_TOOLS
from app.domains.health.categories import ALL_CATEGORIES, LEGACY_CATEGORIES
from app.domains.health.extraction import _SCHEMA, _SYSTEM, extract_health_document
from app.domains.registry import get_spec
from app.llm_gateway import set_gateway_provider
from app.models.document import DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.health import HealthEntry, HealthMeasurement
from app.models.schedule import ProblemEntry
from app.models.transaction import Transaction
from app.models.wiki import WikiClaim
from app.privacy.scope import reachable_scope
from app.services.ingestion import Classification, IncomingFile, ingest
from tests import privacy_world as pw
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.knowledge_factories import make_document
from tests.test_health_extraction import REPLY

NEXT_STEP = 'Use "change" at the top of this page to move it to Financials, Bill / invoice.'
BILL_MESSAGE = ("This looks like a bill or receipt, so it is not filed under Health. Bills are dealt with under "
                "Bills, like any other bill. " + NEXT_STEP)
AMOUNT_KEYS = ("amount_billed", "amount_reimbursed", "amount_owed")


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.settings.DOCUMENTS_DIR", tmp_path / "documents")
    yield
    set_gateway_provider(None)


def _png(tmp_path):
    p = tmp_path / "paper.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nx")
    return str(p)


# (a) the reader neither asks for nor returns amounts
def test_the_prompt_and_schema_have_no_amounts():
    for key in AMOUNT_KEYS:
        assert key not in _SYSTEM
        assert key not in _SCHEMA["properties"]


# (b) a reply that still carries an amount is read without error and stores nothing for it
@pytest.mark.asyncio
async def test_a_reply_with_an_amount_is_read_and_the_amount_is_dropped(tmp_path, session):
    fake = FakeGateway([gateway_text_result(json.dumps({**REPLY, "amount_billed": 45.5, "amount_owed": 3}))])
    got = await extract_health_document(_png(tmp_path), "lab_result", gateway=fake)
    assert got.person_name == "Maria Santos Almeida" and not got.is_bill
    assert not any(hasattr(got, key) for key in AMOUNT_KEYS)

    wire = pw.Wire()
    reply = {**REPLY, "amount_billed": 45.5, "amount_reimbursed": 10, "amount_owed": 35}
    wire.script = [gateway_text_result(json.dumps(reply))] * 2 + [gateway_text_result(json.dumps({"pages": []}))]
    set_gateway_provider(lambda: wire.client())
    result = await ingest(session, IncomingFile("lab.png", b"\x89PNG\r\n\x1a\nlab", DocumentSource.MANUAL, "test"),
                          Classification(Domain.HEALTH, "lab_result", {}))
    assert result.document.status == DocumentStatus.PROCESSED
    entry = store.entry_for(session, result.document.id)
    assert (entry.amount_billed, entry.amount_reimbursed, entry.amount_owed) == (None, None, None)
    from app.domains.fields import load_fields
    assert not any(key in load_fields(result.document) for key in AMOUNT_KEYS)


# (c) the spec no longer knows bills
def test_the_spec_has_neither_bill_category_and_does_not_speak_of_bills():
    spec = get_spec(Domain.HEALTH)
    values = {c.value for c in spec.categories}
    assert not values & {"medical_bill", "claim_statement"}
    assert not ALL_CATEGORIES & {"medical_bill", "claim_statement"}
    assert LEGACY_CATEGORIES == {"medical_bill", "claim_statement"}
    text = spec.description.lower()
    assert "bill" not in text and "reimburs" not in text and "invoice" not in text
    assert not {f.key for f in spec.fields} & set(AMOUNT_KEYS)


# (d) a reply that says the paper is a bill ends NEEDS_ATTENTION, files nothing
async def _ingest_a_bill(session):
    wire = pw.Wire()
    wire.script = [gateway_text_result(json.dumps({"category": "bill", "summary": "A clinic invoice."}))] * 2
    set_gateway_provider(lambda: wire.client())
    result = await ingest(session, IncomingFile("fatura.png", b"\x89PNG\r\n\x1a\nbill", DocumentSource.MANUAL, "test"),
                          Classification(Domain.HEALTH, None, {}))
    return result.document, wire


@pytest.mark.asyncio
async def test_a_bill_is_refused_with_the_three_part_message_and_nothing_is_filed(client, session):
    doc, wire = await _ingest_a_bill(session)
    assert doc.status == DocumentStatus.NEEDS_ATTENTION
    assert BILL_MESSAGE in doc.failure_reason
    assert doc.category is None
    assert session.exec(select(HealthEntry)).all() == []
    assert session.exec(select(HealthMeasurement)).all() == []
    assert session.exec(select(WikiClaim)).all() == []
    assert len(wire.parsed()) == 1                      # a bill is refused after one reading: nothing to compare a second against

    # the paper's page and the Problems entry say the same next step, and the page offers no "Read again"
    import html
    page = html.unescape(client.get(f"/health/documents/{doc.id}").text)
    assert NEXT_STEP in page
    assert "Read again" not in page and "fill the boxes below by hand" not in page
    problem = session.exec(select(ProblemEntry).where(ProblemEntry.ref == f"document:{doc.id}")).one()
    assert problem.next_step == NEXT_STEP
    assert "Tell Pedro" not in problem.next_step and "tell Pedro" not in problem.next_step
    assert "bill or receipt" in problem.why and "dealt with under Bills" in problem.why


@pytest.mark.asyncio
async def test_changing_a_refused_bill_to_financials_bill_files_it_as_a_bill(client, session, monkeypatch):
    """The "change" link works for the refused paper: the refile used by the edit route moves it into the Bills flow."""
    import app.domains.financials.handler as financials
    from app.services.extraction import ExtractedBill
    from app.services.ingestion import refile_document

    doc, _ = await _ingest_a_bill(session)
    assert doc.status == DocumentStatus.NEEDS_ATTENTION and doc.domain == Domain.HEALTH
    assert client.get(f"/documents/{doc.id}/edit").status_code == 200          # the "change" link opens

    async def fake_extract_bill(file_path, gateway=None):
        return ExtractedBill(provider="Clínica Sul", category_hint="other", amount=45.5, currency="EUR",
                             due_date=date(2026, 11, 5), paid_date=None, statement_period="2026-10")

    async def no_wiki(session, document, context=None, **kwargs):
        return None

    async def no_classify(session, transaction, gateway=None):
        return None

    async def classify_bill(file_path, gateway=None):
        return "bill"

    monkeypatch.setattr(financials, "classify_document", classify_bill)
    monkeypatch.setattr(financials, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(financials, "ingest_into_wiki", no_wiki)
    monkeypatch.setattr(financials, "classify_transaction", no_classify)

    moved = await refile_document(session, doc, Classification(Domain.FINANCIALS, "bill", {}))
    assert moved.domain == Domain.FINANCIALS and moved.category == "bill"
    assert moved.status == DocumentStatus.PROCESSED and moved.failure_reason is None
    txn = session.exec(select(Transaction).where(Transaction.document_id == doc.id)).one()
    assert txn.amount == 45.5
    assert session.exec(select(HealthEntry)).all() == []
    assert session.exec(select(ProblemEntry).where(ProblemEntry.ref == f"document:{doc.id}",
                                                   ProblemEntry.resolved_at == None)).all() == []   # noqa: E711


@pytest.mark.asyncio
async def test_the_bill_reply_is_parsed_as_a_bill_with_no_category(tmp_path):
    fake = FakeGateway([gateway_text_result(json.dumps({"category": "bill", "summary": "Invoice."}))])
    got = await extract_health_document(_png(tmp_path), None, gateway=fake)
    assert got.is_bill is True and got.category is None
    assert '"bill"' in fake.requests[0]["system"] or "bill:" in fake.requests[0]["system"]


# (e) the one paper already filed as a bill still opens
@pytest.mark.parametrize("legacy", ["medical_bill", "claim_statement"])
def test_a_paper_filed_under_a_removed_bill_category_still_opens_and_says_it_is_a_bill(client, session, legacy):
    doc = make_document(session, domain=Domain.HEALTH, category=legacy, filename="old.pdf",
                        fields={"provider": "Clínica Sul", "amount_billed": "45,5"})
    r = client.get(f"/health/documents/{doc.id}")
    assert r.status_code == 200
    assert "This paper is a bill. Bills are dealt with under Bills, not Health." in r.text
    assert "Clínica Sul" in r.text


def test_a_normal_paper_page_has_no_bill_line(client, session):
    doc = make_document(session, domain=Domain.HEALTH, category="lab_result", filename="lab.pdf")
    r = client.get(f"/health/documents/{doc.id}")
    assert r.status_code == 200 and "This paper is a bill" not in r.text


# (f) no Ask tool shows an amount, even one a legacy row still holds in its unused column
def test_no_ask_tool_shows_amounts(session):
    pw.set_levels(session, on=False)
    from app.domains.health.extraction import ExtractedHealth, ExtractedMeasurement
    doc = make_document(session, domain=Domain.HEALTH, category="lab_result", filename="a.pdf")
    store.apply_extraction(session, doc, ExtractedHealth(
        person_name="Maria Santos Almeida", document_date=date(2026, 3, 12), provider="Lab Norte", record_kind="test",
        areas=["Heart and circulation"], summary="Maria panel",
        measurements=[ExtractedMeasurement("LDL cholesterol", 162.0, None, "mg/dL", None, 100.0, date(2026, 3, 12),
                                           False, "LDL")]))
    entry = store.entry_for(session, doc.id)
    entry.amount_billed, entry.amount_reimbursed, entry.amount_owed = 4321.5, 1234.5, 3087.0     # a row from before bills left Health
    session.add(entry)
    session.commit()
    assert "health_spending" not in {t.name for t in HEALTH_TOOLS}
    for tool in HEALTH_TOOLS:
        with reachable_scope(session):
            out = tool.run(session, {})
        lowered = out.text.lower()
        assert "billed" not in lowered and "reimbursed" not in lowered and "owed" not in lowered
        assert "spending" not in lowered and "(eur)" not in lowered
        assert not any(figure in out.text for figure in ("4321", "4 321", "1234", "3087"))


# (g) a paper filed under a removed bill category cannot be read again, and is not wiped by trying
def _legacy_paper(session):
    doc = make_document(session, domain=Domain.HEALTH, category="medical_bill", filename="old.pdf",
                        fields={"provider": "Clínica Sul", "amount_billed": "45,5"})
    entry = HealthEntry(document_id=doc.id, entry_date=date(2025, 5, 2), provider="Clínica Sul", summary="Old bill",
                        amount_billed=45.5)
    session.add(entry)
    session.commit()
    session.refresh(entry)
    session.add(HealthMeasurement(entry_id=entry.id, test="Consulta", test_key="consulta", value=1.0))
    session.commit()
    return doc, entry


def test_a_legacy_paper_cannot_be_read_again_and_is_left_intact(client, session):
    doc, entry = _legacy_paper(session)
    r = client.post(f"/health/documents/{doc.id}/reread", follow_redirects=False)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail == {"what": "Not read again.",
                      "why": "Health no longer reads bills, so there is nothing to read it with.",
                      "next_step": NEXT_STEP}
    session.expire_all()
    kept = session.get(type(doc), doc.id)
    assert kept.status == DocumentStatus.PROCESSED and kept.failure_reason is None
    assert json.loads(kept.fields_json) == {"provider": "Clínica Sul", "amount_billed": "45,5"}
    assert store.entry_for(session, doc.id).id == entry.id
    assert len(session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)).all()) == 1


def test_a_legacy_paper_page_offers_no_read_again_and_says_what_to_do(client, session):
    import html
    doc, _ = _legacy_paper(session)
    page = html.unescape(client.get(f"/health/documents/{doc.id}").text)
    assert "Read again" not in page and "/reread" not in page
    assert "This paper is a bill. Bills are dealt with under Bills, not Health." in page
    assert NEXT_STEP in page


# (h) a bill is refused after ONE reading: no second reading, no second look
@pytest.mark.asyncio
async def test_a_bill_reply_makes_exactly_one_gateway_call(tmp_path, monkeypatch):
    from app.domains.health import extraction as ex
    from app.domains.health.reading import read_health_document
    monkeypatch.setattr(ex, "paper_text", lambda path: "Fatura\nTotal 45,50 EUR\n")      # a text layer, so the check would otherwise run
    fake = FakeGateway([gateway_text_result(json.dumps({"category": "bill", "summary": "Invoice."}))] * 3)
    got = await read_health_document(_png(tmp_path), None, gateway=fake)
    assert got.is_bill and len(fake.requests) == 1
