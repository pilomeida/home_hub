import io
import json
from datetime import date

import app.services.pipeline as pipeline_module
import app.services.wiki_engine as wiki_engine_module
from app.services.extraction import ExtractedBill, ExtractedStatement, ExtractedTransaction


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


async def _fake_classify_bill(file_path, client=None):
    return "bill"


async def _fake_classify_statement(file_path, client=None):
    return "statement"


def test_full_bill_ingestion_flow(client, monkeypatch):
    wiki_response = json.dumps({
        "wiki_worthy": True,
        "topic": "Electricity — provider & contract",
        "facts": {"provider": "EDP"},
    })

    async def fake_extract_bill(file_path, client=None):
        return ExtractedBill(
            provider="EDP", category_hint="electricity", amount=87.32, currency="EUR",
            due_date=date(2026, 9, 5), paid_date=None, statement_period="2026-08",
        )

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return await wiki_engine_module.assess_and_update_wiki(
            session, document, transaction, client=_FakeAnthropicClient(wiki_response)
        )

    monkeypatch.setattr(pipeline_module, "classify_document", _fake_classify_bill)
    monkeypatch.setattr(pipeline_module, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline_module, "assess_and_update_wiki", fake_assess_and_update_wiki)

    upload_response = client.post(
        "/bills/upload",
        files={"file": ("edp-august.pdf", io.BytesIO(b"fake-pdf-bytes"), "application/pdf")},
        follow_redirects=False,
    )
    assert upload_response.status_code == 303

    dashboard_response = client.get("/")
    assert dashboard_response.status_code == 200
    assert "electricity" in dashboard_response.text.lower()
    assert "EDP" in dashboard_response.text

    todos_response = client.get("/todos")
    assert "EDP" in todos_response.text

    wiki_list_response = client.get("/wiki")
    assert "Electricity" in wiki_list_response.text


def test_full_statement_ingestion_flow(client, monkeypatch):
    async def fake_extract_statement_transactions(file_path, client=None):
        return ExtractedStatement(
            statement_period="2026-07",
            transactions=[
                ExtractedTransaction(
                    transaction_date=date(2026, 7, 5), description="CONTINENTE MAFRA",
                    amount=42.15, currency="EUR", transaction_type="debit", category_hint="groceries",
                ),
                ExtractedTransaction(
                    transaction_date=date(2026, 7, 10), description="SALARIO EMPRESA X",
                    amount=2200.0, currency="EUR", transaction_type="credit", category_hint="income",
                ),
            ],
        )

    monkeypatch.setattr(pipeline_module, "classify_document", _fake_classify_statement)
    monkeypatch.setattr(pipeline_module, "extract_statement_transactions", fake_extract_statement_transactions)

    upload_response = client.post(
        "/bills/upload",
        files={"file": ("santander-july.pdf", io.BytesIO(b"fake-pdf-bytes"), "application/pdf")},
        follow_redirects=False,
    )
    assert upload_response.status_code == 303
    document_url = upload_response.headers["location"]

    detail_response = client.get(document_url)
    assert detail_response.status_code == 200
    assert "CONTINENTE MAFRA" in detail_response.text
    assert "SALARIO EMPRESA X" in detail_response.text

    dashboard_response = client.get("/")
    assert dashboard_response.status_code == 200
    assert "groceries" in dashboard_response.text.lower()
    # The credit (income) line must not appear in the spend-this-month section total —
    # 2200.0 would be an unmistakable outlier if it leaked into spend.
    assert "2200.00" not in dashboard_response.text

    todos_response = client.get("/todos")
    assert "CONTINENTE" not in todos_response.text
    assert "SALARIO" not in todos_response.text

    wiki_list_response = client.get("/wiki")
    assert "No wiki pages yet." in wiki_list_response.text
