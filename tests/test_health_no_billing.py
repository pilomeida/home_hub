"""Health carries no billing: a bill, receipt or reimbursement statement is a bill (Bills / Insurance), not a health paper."""

import json
from datetime import date

import pytest
from sqlmodel import select

from app.categories.health import store
from app.categories.health.ask import HEALTH_TOOLS
from app.categories.health.kinds import ALL_KINDS
from app.categories.health.extraction import _SCHEMA, _SYSTEM, extract_health_document
from app.categories.registry import get_spec
from app.llm_gateway import set_gateway_provider
from app.models.document import DocumentSource, DocumentStatus
from app.models.category import Category
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
                          Classification(Category.HEALTH, "lab_result", {}))
    assert result.document.status == DocumentStatus.PROCESSED
    entry = store.entry_for(session, result.document.id)
    assert not any(hasattr(entry, key) for key in AMOUNT_KEYS)                  # the columns are gone (step 9, D1)
    from app.categories.fields import load_fields
    assert not any(key in load_fields(result.document) for key in AMOUNT_KEYS)


# (c) the spec no longer knows bills
def test_the_spec_has_neither_bill_category_and_does_not_speak_of_bills():
    spec = get_spec(Category.HEALTH)
    values = {c.value for c in spec.kinds}
    assert not values & {"medical_bill", "claim_statement"}
    assert not ALL_KINDS & {"medical_bill", "claim_statement"}
    text = spec.description.lower()
    assert "bill" not in text and "reimburs" not in text and "invoice" not in text
    assert not {f.key for f in spec.fields} & set(AMOUNT_KEYS)


# (d) a reply that says the paper is a bill ends NEEDS_ATTENTION, files nothing
async def _ingest_a_bill(session):
    wire = pw.Wire()
    wire.script = [gateway_text_result(json.dumps({"category": "bill", "summary": "A clinic invoice."}))] * 2
    set_gateway_provider(lambda: wire.client())
    result = await ingest(session, IncomingFile("fatura.png", b"\x89PNG\r\n\x1a\nbill", DocumentSource.MANUAL, "test"),
                          Classification(Category.HEALTH, None, {}))
    return result.document, wire


@pytest.mark.asyncio
async def test_a_bill_is_refused_with_the_three_part_message_and_nothing_is_filed(client, session):
    doc, wire = await _ingest_a_bill(session)
    assert doc.status == DocumentStatus.NEEDS_ATTENTION
    assert BILL_MESSAGE in doc.failure_reason
    assert doc.paper_kind is None
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
    import app.categories.financials.handler as financials
    from app.services.extraction import ExtractedBill
    from app.services.ingestion import refile_document

    doc, _ = await _ingest_a_bill(session)
    assert doc.status == DocumentStatus.NEEDS_ATTENTION and doc.category == Category.HEALTH
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

    moved = await refile_document(session, doc, Classification(Category.FINANCIALS, "bill", {}))
    assert moved.category == Category.FINANCIALS and moved.paper_kind == "bill"
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
    assert got.is_bill is True and got.paper_kind is None
    assert '"bill"' in fake.requests[0]["system"] or "bill:" in fake.requests[0]["system"]


def test_a_normal_paper_page_has_no_bill_line(client, session):
    doc = make_document(session, category=Category.HEALTH, paper_kind="lab_result", filename="lab.pdf")
    r = client.get(f"/health/documents/{doc.id}")
    assert r.status_code == 200 and "This paper is a bill" not in r.text


# (f) no Ask tool shows an amount, even one a legacy row still holds in its unused column
def test_no_ask_tool_shows_amounts(session):
    pw.set_levels(session, on=False)
    from app.categories.health.extraction import ExtractedHealth, ExtractedMeasurement
    doc = make_document(session, category=Category.HEALTH, paper_kind="lab_result", filename="a.pdf")
    store.apply_extraction(session, doc, ExtractedHealth(
        person_name="Maria Santos Almeida", document_date=date(2026, 3, 12), provider="Lab Norte", record_kind="test",
        areas=["Heart and circulation"], summary="Maria panel",
        measurements=[ExtractedMeasurement("LDL cholesterol", 162.0, None, "mg/dL", None, 100.0, date(2026, 3, 12),
                                           False, "LDL")]))
    entry = store.entry_for(session, doc.id)
    assert "health_spending" not in {t.name for t in HEALTH_TOOLS}
    for tool in HEALTH_TOOLS:
        with reachable_scope(session):
            out = tool.run(session, {})
        lowered = out.text.lower()
        assert "billed" not in lowered and "reimbursed" not in lowered and "owed" not in lowered
        assert "spending" not in lowered and "(eur)" not in lowered
        assert not any(figure in out.text for figure in ("4321", "4 321", "1234", "3087"))


# (h) a bill is refused after ONE reading: no second reading, no second look
@pytest.mark.asyncio
async def test_a_bill_reply_makes_exactly_one_gateway_call(tmp_path, monkeypatch):
    from app.categories.health import extraction as ex
    from app.categories.health.reading import read_health_document
    monkeypatch.setattr(ex, "paper_text", lambda path: "Fatura\nTotal 45,50 EUR\n")      # a text layer, so the check would otherwise run
    fake = FakeGateway([gateway_text_result(json.dumps({"category": "bill", "summary": "Invoice."}))] * 3)
    got = await read_health_document(_png(tmp_path), None, gateway=fake)
    assert got.is_bill and len(fake.requests) == 1
