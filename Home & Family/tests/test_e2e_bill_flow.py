import io
import json
from datetime import date

import app.services.pipeline as pipeline_module
import app.services.wiki_engine as wiki_engine_module
from app.services.extraction import ExtractedBill


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


def test_full_bill_ingestion_flow(client, monkeypatch):
    wiki_response = json.dumps({
        "wiki_worthy": True,
        "topic": "Electricity — provider & contract",
        "facts": {"provider": "EDP"},
    })

    async def fake_extract_bill(image_path, client=None):
        return ExtractedBill(
            provider="EDP", category_hint="electricity", amount=87.32, currency="EUR",
            due_date=date(2026, 9, 5), paid_date=None, statement_period="2026-08",
        )

    async def fake_assess_and_update_wiki(session, document, transaction, client=None):
        return await wiki_engine_module.assess_and_update_wiki(
            session, document, transaction, client=_FakeAnthropicClient(wiki_response)
        )

    monkeypatch.setattr(pipeline_module, "extract_bill", fake_extract_bill)
    monkeypatch.setattr(pipeline_module, "ensure_image", lambda path: path)
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
