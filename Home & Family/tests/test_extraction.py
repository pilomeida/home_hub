import json

import pytest

from app.services.extraction import (
    ClassificationError,
    ExtractedBill,
    ExtractedStatement,
    ExtractionError,
    StatementExtractionError,
    classify_document,
    extract_bill,
    extract_statement_transactions,
)


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


@pytest.mark.asyncio
async def test_extract_bill_parses_valid_response(tmp_path):
    image_path = tmp_path / "page1.png"
    image_path.write_bytes(b"fake-png-bytes")
    response = json.dumps({
        "provider": "EDP", "category_hint": "electricity", "amount": 87.32,
        "currency": "EUR", "due_date": "2026-09-05", "paid_date": None,
        "statement_period": "2026-08",
    })
    client = _FakeAnthropicClient(response)

    result = await extract_bill(str(image_path), client=client)

    assert isinstance(result, ExtractedBill)
    assert result.provider == "EDP"
    assert result.amount == 87.32
    assert result.due_date.isoformat() == "2026-09-05"
    assert result.paid_date is None


@pytest.mark.asyncio
async def test_extract_bill_raises_on_malformed_json(tmp_path):
    image_path = tmp_path / "page1.png"
    image_path.write_bytes(b"fake-png-bytes")
    client = _FakeAnthropicClient("not json")

    with pytest.raises(ExtractionError):
        await extract_bill(str(image_path), client=client)


@pytest.mark.asyncio
async def test_extract_bill_raises_when_provider_missing(tmp_path):
    image_path = tmp_path / "page1.png"
    image_path.write_bytes(b"fake-png-bytes")
    response = json.dumps({"category_hint": "electricity", "amount": 10.0})
    client = _FakeAnthropicClient(response)

    with pytest.raises(ExtractionError):
        await extract_bill(str(image_path), client=client)


class _FakeMessageEmptyContent:
    """Fake message with empty content list to simulate malformed API response."""
    def __init__(self):
        self.content = []


class _FakeMessagesEmptyContent:
    """Fake messages that returns empty content."""
    async def create(self, **kwargs):
        return _FakeMessageEmptyContent()


class _FakeAnthropicClientEmptyContent:
    """Fake client that returns message with empty content list."""
    def __init__(self):
        self.messages = _FakeMessagesEmptyContent()


@pytest.mark.asyncio
async def test_extract_bill_raises_on_empty_content_list(tmp_path):
    """Verify that empty content list raises ExtractionError, not IndexError."""
    image_path = tmp_path / "page1.png"
    image_path.write_bytes(b"fake-png-bytes")
    client = _FakeAnthropicClientEmptyContent()

    with pytest.raises(ExtractionError):
        await extract_bill(str(image_path), client=client)


@pytest.mark.asyncio
async def test_extract_bill_sends_whole_pdf_as_document_block(tmp_path):
    pdf_path = tmp_path / "bill.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake pdf bytes")
    response = json.dumps({
        "provider": "EDP", "category_hint": "electricity", "amount": 42.0,
        "currency": "EUR", "due_date": None, "paid_date": None,
        "statement_period": "2026-08",
    })
    client = _FakeAnthropicClient(response)
    captured = {}
    original_create = client.messages.create

    async def capturing_create(**kwargs):
        captured["messages"] = kwargs["messages"]
        return await original_create(**kwargs)

    client.messages.create = capturing_create

    result = await extract_bill(str(pdf_path), client=client)

    assert result.provider == "EDP"
    content_block = captured["messages"][0]["content"][0]
    assert content_block["type"] == "document"
    assert content_block["source"]["media_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_classify_document_returns_bill(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient(json.dumps({"document_type": "bill"}))

    result = await classify_document(str(pdf_path), client=client)

    assert result == "bill"


@pytest.mark.asyncio
async def test_classify_document_returns_statement(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient(json.dumps({"document_type": "statement"}))

    result = await classify_document(str(pdf_path), client=client)

    assert result == "statement"


@pytest.mark.asyncio
async def test_classify_document_raises_on_malformed_response(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient("not json")

    with pytest.raises(ClassificationError):
        await classify_document(str(pdf_path), client=client)


@pytest.mark.asyncio
async def test_classify_document_raises_on_unexpected_type(tmp_path):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient(json.dumps({"document_type": "receipt"}))

    with pytest.raises(ClassificationError):
        await classify_document(str(pdf_path), client=client)


@pytest.mark.asyncio
async def test_extract_statement_transactions_parses_valid_response(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    response = json.dumps({
        "statement_period": "2026-07",
        "transactions": [
            {"date": "2026-07-05", "description": "CONTINENTE MAFRA", "amount": 42.15,
             "currency": "EUR", "type": "debit", "category_hint": "groceries"},
            {"date": "2026-07-10", "description": "SALARIO EMPRESA X", "amount": 2200.00,
             "currency": "EUR", "type": "credit", "category_hint": "income"},
            {"date": "2026-07-12", "description": "TRANSFERENCIA PARA REVOLUT", "amount": 100.00,
             "currency": "EUR", "type": "transfer", "category_hint": "transfer"},
        ],
    })
    client = _FakeAnthropicClient(response)

    result = await extract_statement_transactions(str(pdf_path), client=client)

    assert isinstance(result, ExtractedStatement)
    assert result.statement_period == "2026-07"
    assert len(result.transactions) == 3
    assert result.transactions[0].description == "CONTINENTE MAFRA"
    assert result.transactions[0].transaction_date.isoformat() == "2026-07-05"
    assert result.transactions[0].transaction_type == "debit"
    assert result.transactions[1].transaction_type == "credit"
    assert result.transactions[2].transaction_type == "transfer"


@pytest.mark.asyncio
async def test_extract_statement_transactions_raises_on_malformed_json(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient("not json")

    with pytest.raises(StatementExtractionError):
        await extract_statement_transactions(str(pdf_path), client=client)


@pytest.mark.asyncio
async def test_extract_statement_transactions_raises_when_transactions_key_missing(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient(json.dumps({"statement_period": "2026-07"}))

    with pytest.raises(StatementExtractionError):
        await extract_statement_transactions(str(pdf_path), client=client)


@pytest.mark.asyncio
async def test_extract_statement_transactions_handles_empty_list(tmp_path):
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    client = _FakeAnthropicClient(json.dumps({"statement_period": "2026-07", "transactions": []}))

    result = await extract_statement_transactions(str(pdf_path), client=client)

    assert result.transactions == []
