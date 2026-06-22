"""Tests for the PDF extraction pipeline."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pdf_extractor import (
    PageText,
    _slugify,
    assemble_recipe_text,
    detect_recipe_boundaries,
    extract_page_texts,
    flag_sparse_pages,
    sample_recipe_indices,
    vision_pass,
)


def test_flag_sparse_pages_identifies_short_text():
    pages = {
        1: PageText(1, "This page has plenty of content to pass the threshold."),
        2: PageText(2, "Short"),
        3: PageText(3, ""),
    }
    sparse = flag_sparse_pages(pages)
    assert 1 not in sparse
    assert 2 in sparse
    assert 3 in sparse


def test_assemble_recipe_text_sorts_and_dedupes_pages():
    texts = {1: "Ingredients", 3: "Method", 2: "Title"}
    result = assemble_recipe_text(texts, [3, 1, 1, 2])
    assert result.index("[Page 1]") < result.index("[Page 2]") < result.index("[Page 3]")
    assert result.count("[Page 1]") == 1  # deduped


def test_assemble_recipe_text_skips_missing_pages():
    texts = {1: "eggs"}
    result = assemble_recipe_text(texts, [1, 99])
    assert "eggs" in result
    assert "[Page 99]" not in result


def test_sample_recipe_indices_returns_n_unique():
    recipes = [{"recipe_title": f"R{i}", "pages": [i]} for i in range(20)]
    indices = sample_recipe_indices(recipes, n=3)
    assert len(indices) == 3
    assert len(set(indices)) == 3
    assert all(0 <= i < 20 for i in indices)


def test_sample_recipe_indices_clamps_to_available():
    recipes = [{"recipe_title": "Only", "pages": [1]}]
    assert sample_recipe_indices(recipes, n=3) == [0]


def test_slugify():
    assert _slugify("Protein Brownie") == "protein-brownie"
    assert _slugify("  Hello, World! ") == "hello-world"
    assert _slugify("Mug Cake – with Tofu") == "mug-cake-with-tofu"


@pytest.mark.asyncio
async def test_detect_recipe_boundaries_parses_json_response():
    mock_response = [
        {"recipe_title": "Protein Brownie", "pages": [20, 21]},
        {"recipe_title": "Mug Cake", "pages": [22, 23]},
    ]
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=json.dumps(mock_response))])
    )
    with patch("app.pdf_extractor._get_client", return_value=mock_client):
        result = await detect_recipe_boundaries(
            {20: "Brownie text", 21: "Calories 267", 22: "Mug Cake text", 23: "Method"}
        )
    assert len(result) == 2
    assert result[0]["recipe_title"] == "Protein Brownie"
    assert 20 in result[0]["pages"]


@pytest.mark.asyncio
async def test_detect_recipe_boundaries_strips_fences():
    wrapped = '```json\n[{"recipe_title": "Test", "pages": [1]}]\n```'
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=wrapped)])
    )
    with patch("app.pdf_extractor._get_client", return_value=mock_client):
        result = await detect_recipe_boundaries({1: "text"})
    assert result[0]["recipe_title"] == "Test"


@pytest.mark.asyncio
async def test_vision_pass_extracts_text_from_pages():
    from PIL import Image
    fake_img = Image.new("RGB", (100, 100), color="white")
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text="KCALS 267  P 37.3g  F 9g  C 16.8g")])
    )
    with patch("app.pdf_extractor.convert_from_path", return_value=[fake_img]), \
         patch("app.pdf_extractor._get_client", return_value=mock_client):
        result = await vision_pass("/fake/book.pdf", [20])
    assert 20 in result
    assert "37.3g" in result[20]


@pytest.mark.asyncio
async def test_vision_pass_returns_empty_for_no_pages():
    result = await vision_pass("/fake/book.pdf", [])
    assert result == {}
