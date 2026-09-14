import json

import pytest
from sqlmodel import select

from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.transaction import Category, Transaction
from app.models.wiki import WikiPage
from app.services.wiki_engine import assess_and_update_wiki


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _FakeMessages:
    def __init__(self, response_text):
        self._response_text = response_text

    async def create(self, **kwargs):
        return _FakeMessage(self._response_text)


class _FakeAnthropicClient:
    def __init__(self, response_text):
        self.messages = _FakeMessages(response_text)


def _make_document_and_transaction(session):
    document = Document(
        filename="bill.pdf", file_path="/tmp/bill.pdf", content_hash="hash1",
        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=87.32, currency="EUR", statement_period="2026-08",
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)
    return document, transaction


@pytest.mark.asyncio
async def test_creates_new_wiki_page_when_worthy(session):
    document, transaction = _make_document_and_transaction(session)
    response = json.dumps({
        "wiki_worthy": True,
        "topic": "Electricity — provider & contract",
        "facts": {"provider": "EDP", "tariff": "Bi-horário"},
    })
    client = _FakeAnthropicClient(response)

    change = await assess_and_update_wiki(session, document, transaction, client=client)

    assert change is not None
    page = session.exec(
        select(WikiPage).where(WikiPage.topic == "Electricity — provider & contract")
    ).first()
    assert page is not None
    assert json.loads(page.facts_json) == {"provider": "EDP", "tariff": "Bi-horário"}


@pytest.mark.asyncio
async def test_returns_none_when_not_worthy(session):
    document, transaction = _make_document_and_transaction(session)
    response = json.dumps({"wiki_worthy": False, "topic": "", "facts": {}})
    client = _FakeAnthropicClient(response)

    change = await assess_and_update_wiki(session, document, transaction, client=client)

    assert change is None


@pytest.mark.asyncio
async def test_updates_existing_page_and_records_change(session):
    document, transaction = _make_document_and_transaction(session)
    existing_page = WikiPage(
        topic="Electricity — provider & contract",
        facts_json=json.dumps({"provider": "EDP", "tariff": "Simples"}),
    )
    session.add(existing_page)
    session.commit()

    response = json.dumps({
        "wiki_worthy": True,
        "topic": "Electricity — provider & contract",
        "facts": {"tariff": "Bi-horário"},
    })
    client = _FakeAnthropicClient(response)

    change = await assess_and_update_wiki(session, document, transaction, client=client)

    assert change is not None
    assert change.fact_key == "tariff"
    session.refresh(existing_page)
    assert json.loads(existing_page.facts_json) == {"provider": "EDP", "tariff": "Bi-horário"}


@pytest.mark.asyncio
async def test_no_op_when_facts_unchanged(session):
    document, transaction = _make_document_and_transaction(session)
    existing_page = WikiPage(
        topic="Electricity — provider & contract",
        facts_json=json.dumps({"provider": "EDP"}),
    )
    session.add(existing_page)
    session.commit()

    response = json.dumps({
        "wiki_worthy": True,
        "topic": "Electricity — provider & contract",
        "facts": {"provider": "EDP"},
    })
    client = _FakeAnthropicClient(response)

    change = await assess_and_update_wiki(session, document, transaction, client=client)

    assert change is None


@pytest.mark.asyncio
async def test_new_wiki_page_gets_financials_domain(session):
    from app.models.domain import Domain

    document, transaction = _make_document_and_transaction(session)
    response = json.dumps({
        "wiki_worthy": True,
        "topic": "Water — provider & contract",
        "facts": {"provider": "EPAL"},
    })
    client = _FakeAnthropicClient(response)

    await assess_and_update_wiki(session, document, transaction, client=client)

    page = session.exec(select(WikiPage).where(WikiPage.topic == "Water — provider & contract")).first()
    assert page.domain == Domain.FINANCIALS
