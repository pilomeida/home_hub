"""PLAN step 8a: Identity & Legal. Done when (step 8): a passport is uploaded, read, and its expiry reminder appears on
To-Dos and reaches the phone once. The category is read only at High, binding levels or not."""

import json
from datetime import date, datetime, timedelta

import pytest
from sqlmodel import select

from app.categories.fields import load_fields
from app.categories.registry import get_spec
from app.llm_gateway import call_level, set_gateway_provider
from app.models.category import Category
from app.models.document import DocumentSource, DocumentStatus
from app.models.subject import Subject, SubjectKind
from app.models.todo import Todo
from app.services import ingestion, notices
from app.services.ingestion import Classification, IncomingFile
from app.ui import shell
from tests.fakes.fake_gateway import FakeGateway, gateway_text_result
from tests.fakes.text_pdf import text_pdf_bytes

EXPIRES = date.today() + timedelta(days=400)
PASSPORT = ["REPUBLICA PORTUGUESA PASSAPORTE", "Apelido ALMEIDA Nome PEDRO SANTOS", "N.o CB123456",
            f"Data de emissao 02/03/2021 Valido ate {EXPIRES.strftime('%d/%m/%Y')}"]
READING = {"holder": "Pedro Santos Almeida", "number": "CB123456", "issued_on": "2021-03-02",
           "expires_on": EXPIRES.isoformat(), "country": "Portugal"}


class TierRecordingGateway(FakeGateway):
    """The fake gateway, also keeping the level each call was actually made at (the real client's checkpoint)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.levels: list = []

    async def run(self, workload_type, system, user=None, **kw):
        self.levels.append(call_level(kw.get("sensitivity"), kw.get("redaction"))[0])
        return await super().run(workload_type, system, user, **kw)


@pytest.fixture()
def gateway():
    fake = TierRecordingGateway()
    set_gateway_provider(lambda: fake)
    yield fake
    set_gateway_provider(None)


async def _passport(session, gateway, reading=READING, typed=None):
    gateway._bodies.append(gateway_text_result(json.dumps(reading)))
    return (await ingestion.ingest(session, IncomingFile("passport.pdf", text_pdf_bytes(PASSPORT), DocumentSource.MANUAL),
                                   Classification(Category.IDENTITY, "passport", typed or {}))).document


def test_identity_is_registered_forced_high_and_under_papers():
    spec = get_spec(Category.IDENTITY)
    assert spec.label == "Identity & Legal" and spec.forced_tier.value == "house_high"
    assert [k.value for k in spec.kinds] == ["id_card", "passport", "driving_licence", "residence", "will"]
    papers = next(s for s in shell.sections() if s.key == "papers")
    assert any(l.url == "/identity" for l in papers.links) and shell.active("/identity/upload").key == "papers"


@pytest.mark.asyncio
async def test_a_passport_is_read_at_high_and_its_renewal_is_a_todo(session, inbox_documents_dir, gateway):
    doc = await _passport(session, gateway, typed={"notes": "In the safe"})
    assert doc.status == DocumentStatus.PROCESSED and doc.failure_reason is None
    assert [str(getattr(l, "value", l)) for l in gateway.levels] == ["house_high"]   # not binding, still High
    fields = load_fields(doc)
    assert (fields["holder"], fields["document_number"], fields["expires_on"]) == (
        "Pedro Santos Almeida", "CB123456", EXPIRES.isoformat())
    assert fields["notes"] == "In the safe"
    assert session.exec(select(Subject).where(Subject.kind == SubjectKind.PERSON,
                                              Subject.name == "Pedro Santos Almeida")).first() is not None
    [todo] = session.exec(select(Todo).where(Todo.document_id == doc.id)).all()
    assert todo.category == Category.IDENTITY and todo.due_date == EXPIRES - timedelta(days=90)
    assert todo.title == f"Renew Pedro's passport (expires {EXPIRES.isoformat()})"
    assert "CB123456" not in todo.title                                                # the number stays on the paper


@pytest.mark.asyncio
async def test_the_reminder_reaches_the_phone_once_without_the_number(session, inbox_documents_dir, gateway):
    doc = await _passport(session, gateway)
    due = EXPIRES - timedelta(days=90)
    assert [f for f in notices.current(session, due - timedelta(days=1)) if f.kind == notices.PAPER_DATE_DUE] == []
    [found] = [f for f in notices.current(session, due) if f.kind == notices.PAPER_DATE_DUE]
    assert found.text == f"Pedro's passport expires on {EXPIRES.isoformat()}: see To-Dos." and found.link == "/todos"
    when = datetime.combine(due, datetime.min.time()).replace(hour=10)
    plan = notices.refresh(session, when)
    assert [n.kind for n in plan.new].count(notices.PAPER_DATE_DUE) == 1
    notices.mark_sent(session, plan, when)
    again = notices.refresh(session, when + timedelta(days=5))
    assert not [n for n in again.new + again.reminders if n.kind == notices.PAPER_DATE_DUE]


@pytest.mark.asyncio
async def test_no_expiry_found_says_so_and_typing_it_makes_the_reminder(session, inbox_documents_dir, gateway):
    doc = await _passport(session, gateway, reading={**READING, "expires_on": None})
    assert "expiry date was not found" in doc.failure_reason
    assert session.exec(select(Todo).where(Todo.document_id == doc.id)).all() == []
    values = {**load_fields(doc), "expires_on": EXPIRES.isoformat()}
    await ingestion.update_document_fields(session, doc, values)
    session.refresh(doc)
    assert doc.failure_reason is None
    assert len(session.exec(select(Todo).where(Todo.document_id == doc.id)).all()) == 1


@pytest.mark.asyncio
async def test_the_pages_and_the_overview_card(client, session, inbox_documents_dir, gateway):
    from app.categories.identity.overview import identity_overview_card
    doc = await _passport(session, gateway)
    landing = client.get("/identity").text
    assert "Pedro Santos Almeida" in landing and f"/identity/documents/{doc.id}" in landing
    assert f"expires {EXPIRES.isoformat()}" in landing
    assert "CB123456" in client.get(f"/identity/documents/{doc.id}").text
    bad = client.post("/identity/upload", data={"category": "passport"})
    assert bad.status_code == 400 and "No file was chosen" in bad.text
    texts = [l.text for l in identity_overview_card(session, date.today()).lines]
    assert texts == ["1 paper on file", f"next expiry {EXPIRES.isoformat()}"]
    assert not any("Pedro" in t or "CB123456" in t for t in texts)


@pytest.mark.asyncio
async def test_a_reading_that_fails_says_so_in_plain_words_not_the_error(client, session, inbox_documents_dir,
                                                                        gateway):
    gateway._bodies.append(gateway_text_result("not json"))
    doc = (await ingestion.ingest(session, IncomingFile("passport.pdf", text_pdf_bytes(PASSPORT), DocumentSource.MANUAL),
                                  Classification(Category.IDENTITY, "passport", {}))).document
    assert doc.status == DocumentStatus.NEEDS_ATTENTION
    page = client.get(f"/identity/documents/{doc.id}").text
    assert "Not read." in page and "No reminder yet" not in page
    assert "IdentityReadingError" not in page and "Traceback" not in page


def test_changing_an_identity_level_is_refused_naming_identity(client):
    from app.main import app
    from app.routers.privacy import get_privacy_gateway
    from tests.test_health_domain import _NoJobs
    app.dependency_overrides[get_privacy_gateway] = lambda: _NoJobs()
    r = client.post("/privacy/types", data={"domain": "identity", "category": "passport", "tier": "house_low"},
                    follow_redirects=True)
    assert "Identity &amp; Legal papers are always High, so their level cannot be changed." in r.text
    assert "Health papers are always High" not in r.text
    app.dependency_overrides.pop(get_privacy_gateway, None)
