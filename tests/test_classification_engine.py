import json

import pytest

from app.models.transaction import Category, Nature
from app.services.classification_engine import (
    MerchantResolutionError,
    ResolvedMerchant,
    normalize_provider,
    resolve_merchant_via_llm,
)


def test_normalize_provider_strips_trailing_store_code():
    assert normalize_provider("MODELO HIPER 2640-MAFR") == "modelo hiper"


def test_normalize_provider_strips_trailing_location_word():
    assert normalize_provider("MODELO HIPER MAFRA") == "modelo hiper"


def test_normalize_provider_leaves_bare_name_unchanged():
    assert normalize_provider("MODELO HIPER") == "modelo hiper"


def test_normalize_provider_strips_repeated_trailing_location_words():
    assert normalize_provider("ALDI MAFRA MAFRA") == "aldi"


def test_normalize_provider_strips_single_trailing_location_word():
    assert normalize_provider("INTERMARCHE MAFRA") == "intermarche"


def test_normalize_provider_does_not_strip_fused_substring():
    # "ACMAFRA" ends in "mafra" as a substring of one fused word, not a
    # separate trailing location token preceded by whitespace — must not
    # be stripped, since that would corrupt an unrelated merchant name.
    assert normalize_provider("AUTOMAFRA - PNEUS ACMAFRA") == "automafra - pneus acmafra"


def test_normalize_provider_collapses_whitespace():
    assert normalize_provider("  MODELO   HIPER  ") == "modelo hiper"


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
async def test_resolve_merchant_via_llm_parses_valid_response():
    response = json.dumps({
        "canonical_name": "Modelo Hiper",
        "category": "groceries",
        "nature": "essential",
    })
    client = _FakeAnthropicClient(response)

    result = await resolve_merchant_via_llm("MODELO HIPER 2640-MAFR", client=client)

    assert isinstance(result, ResolvedMerchant)
    assert result.canonical_name == "Modelo Hiper"
    assert result.category == Category.GROCERIES
    assert result.nature == Nature.ESSENTIAL


@pytest.mark.asyncio
async def test_resolve_merchant_via_llm_raises_on_malformed_json():
    client = _FakeAnthropicClient("not json")

    with pytest.raises(MerchantResolutionError):
        await resolve_merchant_via_llm("SOME PROVIDER", client=client)


@pytest.mark.asyncio
async def test_resolve_merchant_via_llm_raises_on_invalid_category():
    response = json.dumps({
        "canonical_name": "Some Shop", "category": "not_a_real_category", "nature": "essential",
    })
    client = _FakeAnthropicClient(response)

    with pytest.raises(MerchantResolutionError):
        await resolve_merchant_via_llm("SOME SHOP", client=client)
