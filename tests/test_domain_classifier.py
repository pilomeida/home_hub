import json

import pytest

from app.domains.registry import implemented_domains
from app.models.domain import Domain
from app.services.domain_classifier import (
    DomainClassificationError, DomainSuggestion, build_system_prompt, suggest_domain_and_category,
)


class _Msg:
    def __init__(self, text):
        self.content = [type("C", (), {"text": text})()]


class _FakeClient:
    def __init__(self, text):
        self.calls = []
        outer = self

        class _Messages:
            async def create(self, **kwargs):
                outer.calls.append(kwargs)
                return _Msg(text)

        self.messages = _Messages()


def _answer(**data):
    return _FakeClient(json.dumps(data))


@pytest.fixture()
def pdf(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    return str(path)


def test_prompt_is_built_from_the_registry(two_domains):
    prompt = build_system_prompt(implemented_domains())
    for spec in two_domains.values():
        assert f'"{spec.domain.value}"' in prompt and spec.description in prompt
        for category in spec.categories:
            assert f'"{category.value}"' in prompt and category.description in prompt
    assert "may be null" in prompt   # Money infers its own category


@pytest.mark.asyncio
async def test_valid_answer(pdf, two_domains):
    client = _answer(domain="house", category="manual", confidence=0.93, reason="a manual")
    result = await suggest_domain_and_category(pdf, "Email subject: boiler", client=client)
    assert result == DomainSuggestion(Domain.HOUSE, "manual", 0.93, "a manual")
    user_content = client.calls[0]["messages"][0]["content"]
    assert any("Email subject: boiler" in b.get("text", "") for b in user_content)
    assert client.calls[0]["model"] == "claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_null_category_allowed_only_for_inferring_domains(pdf, two_domains):
    ok = await suggest_domain_and_category(pdf, client=_answer(domain="financials", category=None, confidence=0.9, reason="x"))
    assert ok.domain == Domain.FINANCIALS and ok.category is None
    bad = await suggest_domain_and_category(pdf, client=_answer(domain="house", category=None, confidence=0.9, reason="x"))
    assert bad.domain is None


@pytest.mark.asyncio
async def test_answer_outside_registry_becomes_empty(pdf, two_domains):
    wrong_cat = await suggest_domain_and_category(pdf, client=_answer(domain="house", category="bill", confidence=0.99, reason="x"))
    unknown_domain = await suggest_domain_and_category(pdf, client=_answer(domain="health", category="x", confidence=0.99, reason="x"))
    assert wrong_cat.domain is None and wrong_cat.confidence == 0.0
    assert unknown_domain.domain is None


@pytest.mark.asyncio
async def test_null_domain_keeps_reason(pdf, two_domains):
    result = await suggest_domain_and_category(pdf, client=_answer(domain=None, category=None, confidence=0.2, reason="a birthday card"))
    assert result.domain is None and result.note == "a birthday card"


@pytest.mark.asyncio
async def test_confidence_is_clamped(pdf, two_domains):
    result = await suggest_domain_and_category(pdf, client=_answer(domain="house", category="manual", confidence=7, reason="x"))
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_unreadable_file_skips_the_model(tmp_path, two_domains):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00")
    client = _FakeClient("unused")
    result = await suggest_domain_and_category(str(video), client=client)
    assert client.calls == [] and result.domain is None
    assert "can't be read automatically" in result.note


@pytest.mark.asyncio
async def test_garbage_response_raises(pdf, two_domains):
    with pytest.raises(DomainClassificationError):
        await suggest_domain_and_category(pdf, client=_FakeClient("not json"))
