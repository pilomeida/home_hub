"""The domain classifier goes through the gateway: `vision_classification`
with attachments, registry-built system prompt, no model names."""

import json

import pytest

from app.domains.registry import implemented_domains
from app.models.domain import Domain
from app.services.domain_classifier import (
    DomainClassificationError, DomainSuggestion, build_system_prompt, suggest_domain_and_category,
)
from tests.fakes.fake_gateway import FakeGateway

_VISION_CLASSIFICATION = "vision_classification"


def _answer(**data):
    return FakeGateway([{
        "text": json.dumps(data), "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }])


def _assert_wire(fake, expect_context: bool):
    req = fake.requests[0]
    assert req["workload_type"] == _VISION_CLASSIFICATION
    assert "model" not in req and "thinking" not in req and req["max_tokens"] is None
    assert isinstance(req["system"], str) and "You sort household documents" in req["system"]
    assert "Classify this document as JSON." in req["user"]
    assert len(req["attachments"]) == 1
    if expect_context:
        assert "Email subject: boiler" in req["user"]
    schema = req["response_schema"]
    assert schema["properties"]["confidence"]["minimum"] == 0
    assert schema["properties"]["confidence"]["maximum"] == 1
    assert schema["properties"]["domain"]["type"] == ["string", "null"]


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
async def test_valid_answer_and_wire_shape(pdf, two_domains):
    client = _answer(domain="house", category="manual", confidence=0.93, reason="a manual")
    result = await suggest_domain_and_category(pdf, "Email subject: boiler", gateway=client)
    assert result == DomainSuggestion(Domain.HOUSE, "manual", 0.93, "a manual")
    _assert_wire(client, expect_context=True)


@pytest.mark.asyncio
async def test_null_category_allowed_only_for_inferring_domains(pdf, two_domains):
    ok = await suggest_domain_and_category(pdf, gateway=_answer(domain="financials", category=None, confidence=0.9, reason="x"))
    assert ok.domain == Domain.FINANCIALS and ok.category is None
    bad = await suggest_domain_and_category(pdf, gateway=_answer(domain="house", category=None, confidence=0.9, reason="x"))
    assert bad.domain is None


@pytest.mark.asyncio
async def test_answer_outside_registry_becomes_empty(pdf, two_domains):
    wrong_cat = await suggest_domain_and_category(pdf, gateway=_answer(domain="house", category="bill", confidence=0.99, reason="x"))
    unknown_domain = await suggest_domain_and_category(pdf, gateway=_answer(domain="health", category="x", confidence=0.99, reason="x"))
    assert wrong_cat.domain is None and wrong_cat.confidence == 0.0
    assert unknown_domain.domain is None


@pytest.mark.asyncio
async def test_null_domain_keeps_reason(pdf, two_domains):
    result = await suggest_domain_and_category(pdf, gateway=_answer(domain=None, category=None, confidence=0.2, reason="a birthday card"))
    assert result.domain is None and result.note == "a birthday card"


@pytest.mark.asyncio
async def test_confidence_is_clamped(pdf, two_domains):
    result = await suggest_domain_and_category(pdf, gateway=_answer(domain="house", category="manual", confidence=7, reason="x"))
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_unreadable_file_skips_the_model(tmp_path, two_domains):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00")
    client = _answer(domain=None)
    result = await suggest_domain_and_category(str(video), gateway=client)
    assert client.requests == [] and result.domain is None
    assert "can't be read automatically" in result.note


@pytest.mark.asyncio
async def test_garbage_response_raises(pdf, two_domains):
    garbage = FakeGateway([{"text": "not json", "stop_reason": "end_turn", "usage": {}}])
    with pytest.raises(DomainClassificationError):
        await suggest_domain_and_category(pdf, gateway=garbage)
