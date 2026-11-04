"""The domain classifier goes through the gateway: `vision_classification`
with attachments, registry-built system prompt, no model names."""

import json

import pytest

from app.categories.registry import implemented_categories
from app.models.category import Category
from app.services.category_classifier import (
    CategoryClassificationError, CategorySuggestion, build_system_prompt, suggest_category_and_kind,
)
from tests.fakes.fake_gateway import FakeGateway

# These call readers directly; production calls them inside a privacy scope (tests/conftest.py).
pytestmark = pytest.mark.usefixtures("levels_not_binding")

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


def test_prompt_is_built_from_the_registry(two_categories):
    prompt = build_system_prompt(implemented_categories())
    for spec in two_categories.values():
        assert f'"{spec.category.value}"' in prompt and spec.description in prompt
        for category in spec.kinds:
            assert f'"{category.value}"' in prompt and category.description in prompt
    assert "may be null" in prompt   # Money infers its own category


@pytest.mark.asyncio
async def test_valid_answer_and_wire_shape(pdf, two_categories):
    client = _answer(domain="house", category="manual", confidence=0.93, reason="a manual")
    result = await suggest_category_and_kind(pdf, "Email subject: boiler", gateway=client)
    assert result == CategorySuggestion(Category.HOUSE, "manual", 0.93, "a manual")
    _assert_wire(client, expect_context=True)


@pytest.mark.asyncio
async def test_null_category_allowed_only_for_inferring_domains(pdf, two_categories):
    ok = await suggest_category_and_kind(pdf, gateway=_answer(domain="financials", category=None, confidence=0.9, reason="x"))
    assert ok.category == Category.FINANCIALS and ok.paper_kind is None
    bad = await suggest_category_and_kind(pdf, gateway=_answer(domain="house", category=None, confidence=0.9, reason="x"))
    assert bad.category is None


@pytest.mark.asyncio
async def test_answer_outside_registry_becomes_empty(pdf, two_categories):
    wrong_cat = await suggest_category_and_kind(pdf, gateway=_answer(domain="house", category="bill", confidence=0.99, reason="x"))
    unknown_domain = await suggest_category_and_kind(pdf, gateway=_answer(domain="health", category="x", confidence=0.99, reason="x"))
    assert wrong_cat.category is None and wrong_cat.confidence == 0.0
    assert unknown_domain.category is None


@pytest.mark.asyncio
async def test_null_domain_keeps_reason(pdf, two_categories):
    result = await suggest_category_and_kind(pdf, gateway=_answer(domain=None, category=None, confidence=0.2, reason="a birthday card"))
    assert result.category is None and result.note == "a birthday card"


@pytest.mark.asyncio
async def test_confidence_is_clamped(pdf, two_categories):
    result = await suggest_category_and_kind(pdf, gateway=_answer(domain="house", category="manual", confidence=7, reason="x"))
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_unreadable_file_skips_the_model(tmp_path, two_categories):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"\x00\x00")
    client = _answer(domain=None)
    result = await suggest_category_and_kind(str(video), gateway=client)
    assert client.requests == [] and result.category is None
    assert "can't be read automatically" in result.note


@pytest.mark.asyncio
async def test_garbage_response_raises(pdf, two_categories):
    garbage = FakeGateway([{"text": "not json", "stop_reason": "end_turn", "usage": {}}])
    with pytest.raises(CategoryClassificationError):
        await suggest_category_and_kind(pdf, gateway=garbage)


@pytest.mark.asyncio
@pytest.mark.parametrize("paper_kind", ["statement_positions", "loan_history"])
async def test_non_classifiable_category_from_the_model_is_treated_as_unknown(pdf, paper_kind):
    # real registry: these two categories are chosen on the loans upload page, never by the classifier
    prompt = build_system_prompt(implemented_categories())
    assert f'"{paper_kind}"' not in prompt
    result = await suggest_category_and_kind(
        pdf, gateway=_answer(domain="financials", category=paper_kind, confidence=0.99, reason="x"))
    assert result.category is None and result.confidence == 0.0
