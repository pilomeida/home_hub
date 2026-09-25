import json

import pytest
from sqlmodel import select

from app.domains import registry
from app.domains.base import FactPolicy
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.wiki import WikiLogEntry, WikiOperation, WikiPage
from app.services import wiki_store
from app.services.wiki_engine import (
    WikiAssessmentError, assess_document_for_wiki, claims_from_fields, ingest_into_wiki,
)
from tests.domain_fakes import make_fake_spec


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeMessage(self._response_text)


class _FakeAnthropicClient:
    def __init__(self, response_text):
        self.messages = _FakeMessages(response_text)


class _ExplodingClient:
    class messages:  # noqa: N801
        @staticmethod
        async def create(**kwargs):
            raise AssertionError("the LLM must not be called")


@pytest.fixture()
def guided_domain(monkeypatch):
    spec = make_fake_spec(wiki_guidance="Record provider facts.")
    monkeypatch.setattr(registry, "_specs_cache", {spec.domain: spec})
    return spec


def _document(session, category="manual", fields=None, content_hash="h1"):
    document = Document(
        filename="doc.pdf", file_path="/tmp/doc.pdf", content_hash=content_hash,
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
        domain=Domain.HOUSE, category=category, fields_json=json.dumps(fields or {}),
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def test_claims_from_fields_builds_entity_claims(session, fake_domain):
    document = _document(session, fields={"item_name": "Heat  Pump", "seen_on": "2026-03-01"})

    claims = claims_from_fields(fake_domain, document)

    assert {(c.page.page_type, c.page.title, c.page.entity_key) for c in claims} == {("fake.item", "Heat  Pump", "heat pump")}
    by_key = {c.key: c for c in claims}
    assert by_key["type"].value == "Manual" and by_key["type"].label == "Type"
    assert by_key["seen"].value == "2026-03-01" and by_key["seen"].policy == FactPolicy.LATEST


def test_claims_from_fields_skips_unnamed_or_unrelated_documents(session, fake_domain):
    assert claims_from_fields(fake_domain, _document(session, fields={"seen_on": "2026-03-01"})) == []
    assert claims_from_fields(fake_domain, _document(session, category="clip", fields={"side": "in"}, content_hash="h2")) == []


@pytest.mark.asyncio
async def test_assess_returns_topic_claims_and_offers_existing_titles(session, guided_domain):
    session.add(WikiPage(topic="Electricity — provider & contract", domain=Domain.HOUSE))
    session.commit()
    document = _document(session)
    client = _FakeAnthropicClient(json.dumps({"pages": [
        {"title": "Electricity — provider & contract", "summary": "EDP, bi-hourly", "facts": {"provider": "EDP", "tariff": "Bi-horário"}},
    ]}))

    claims = await assess_document_for_wiki(session, document, "Provider: EDP", client=client)

    assert {(c.page.page_type, c.page.title, c.key, c.value) for c in claims} == {
        ("topic", "Electricity — provider & contract", "provider", "EDP"),
        ("topic", "Electricity — provider & contract", "tariff", "Bi-horário"),
    }
    assert claims[0].page.summary == "EDP, bi-hourly"
    call = client.messages.calls[0]
    assert "Record provider facts." in call["system"]
    assert "Electricity — provider & contract" in call["system"]
    assert "Provider: EDP" in call["messages"][0]["content"]


@pytest.mark.asyncio
async def test_assess_is_a_no_op_without_guidance(session, fake_domain):
    assert await assess_document_for_wiki(session, _document(session), "ctx", client=_ExplodingClient()) == []


@pytest.mark.asyncio
async def test_assess_raises_on_unparseable_reply(session, guided_domain):
    with pytest.raises(WikiAssessmentError):
        await assess_document_for_wiki(session, _document(session), "ctx", client=_FakeAnthropicClient("not json"))


@pytest.mark.asyncio
async def test_ingest_applies_field_and_llm_claims_and_logs(session, guided_domain):
    document = _document(session, fields={"item_name": "Boiler"})
    client = _FakeAnthropicClient(json.dumps({"pages": [{"title": "Gas supply", "summary": "Galp", "facts": {"provider": "Galp"}}]}))

    report = await ingest_into_wiki(session, document, context="Provider: Galp", client=client)

    titles = {p.topic for p in session.exec(select(WikiPage)).all()}
    assert titles == {"Boiler", "Gas supply"}
    assert all(p.domain == Domain.HOUSE for p in session.exec(select(WikiPage)).all())
    entry = session.get(WikiLogEntry, report.log_entry_id)
    assert entry.operation == WikiOperation.INGEST and entry.document_id == document.id


@pytest.mark.asyncio
async def test_ingest_without_context_never_calls_the_llm(session, guided_domain):
    document = _document(session, fields={"item_name": "Boiler"})
    report = await ingest_into_wiki(session, document, client=_ExplodingClient())
    assert report.added == 1  # the "type" fact from the category


@pytest.mark.asyncio
async def test_ingest_records_a_log_entry_even_with_nothing_to_record(session, fake_domain):
    document = _document(session, category="clip", fields={"side": "in"})
    report = await ingest_into_wiki(session, document, operation=WikiOperation.EDIT)
    assert report.page_ids == []
    assert session.get(WikiLogEntry, report.log_entry_id).operation == WikiOperation.EDIT
    assert wiki_store.recent_log(session)[0].description == "doc.pdf → no wiki pages"