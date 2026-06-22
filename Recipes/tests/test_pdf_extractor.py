"""Tests for the PDF extraction pipeline."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pdf_extractor import (
    PageText,
    PdfIngestionSession,
    _slugify,
    assemble_recipe_text,
    create_session,
    detect_recipe_boundaries,
    extract_page_texts,
    flag_sparse_pages,
    get_pending_batch,
    load_session,
    sample_recipe_indices,
    save_session,
    vision_pass,
)
import app.pdf_extractor as _pe


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


@pytest.mark.asyncio
async def test_vision_pass_non_contiguous_pages():
    """Verify correct page-number mapping for non-contiguous sparse pages."""
    from PIL import Image
    # Three sparse pages: 1, 3, 5 — rendering pages 1-5 (5 images)
    fake_imgs = [Image.new("RGB", (100, 100)) for _ in range(5)]
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text="extracted text")])
    )
    with patch("app.pdf_extractor.convert_from_path", return_value=fake_imgs), \
         patch("app.pdf_extractor._get_client", return_value=mock_client):
        result = await vision_pass("/fake/book.pdf", [1, 3, 5])
    # Only pages 1, 3, 5 should be in result (pages 2 and 4 filtered out)
    assert set(result.keys()) == {1, 3, 5}
    assert 2 not in result
    assert 4 not in result


# ── Session management tests ──────────────────────────────────────────────────

def _make_session(**overrides) -> PdfIngestionSession:
    defaults = dict(
        session_id="s1",
        pdf_path="/tmp/book.pdf",
        book_title="Test Book",
        book_slug="test-book",
        all_recipes=[{"recipe_title": "Brownie", "pages": [1, 2], "status": "pending"}],
        sampled_indices=[0],
        extracted={},
        extraction_complete=False,
        created_at="2026-06-22T00:00:00",
    )
    defaults.update(overrides)
    return PdfIngestionSession(**defaults)


def test_save_and_load_session(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_SESSIONS_DIR", tmp_path)
    session = _make_session()
    save_session(session)
    loaded = load_session("s1")
    assert loaded.book_title == "Test Book"
    assert loaded.all_recipes[0]["recipe_title"] == "Brownie"
    assert not loaded.extraction_complete


def test_get_pending_batch_excludes_non_pending():
    session = _make_session(
        sampled_indices=[0, 1, 2],
        extracted={
            "0": {"dish_name": "A", "status": "pending"},
            "1": {"dish_name": "B", "status": "approved"},
            "2": {"dish_name": "C", "status": "pending"},
        },
        extraction_complete=True,
    )
    batch = get_pending_batch(session, n=3)
    indices = [i for i, _ in batch]
    assert 0 in indices
    assert 1 not in indices
    assert 2 in indices


def test_get_pending_batch_respects_n():
    session = _make_session(
        sampled_indices=[0, 1, 2],
        extracted={
            "0": {"dish_name": "A", "status": "pending"},
            "1": {"dish_name": "B", "status": "pending"},
            "2": {"dish_name": "C", "status": "pending"},
        },
        extraction_complete=True,
    )
    batch = get_pending_batch(session, n=2)
    assert len(batch) == 2


@pytest.mark.asyncio
async def test_create_session_runs_full_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_SESSIONS_DIR", tmp_path)

    fake_page_texts = {
        1: PageText(1, "Recipe: Brownie. Ingredients: egg. Method: microwave 3 min."),
        2: PageText(2, ""),
    }
    fake_boundaries = [{"recipe_title": "Brownie", "pages": [1, 2]}]
    fake_extracted = {
        "dish_name": "Brownie", "distinguishing_feature": None,
        "type": "sweet", "subtype": "dessert", "macro_tags": [],
        "calories_per_portion": 267, "ingredients": ["egg"],
        "prep_time_minutes": 1, "cook_time_minutes": 3, "portions": 1,
        "instructions": "Microwave 3 min.", "missing_critical_info": False,
        "cooking_types": ["microwave"], "protein_g": 37, "fat_g": 9,
        "carbs_g": 17, "fiber_g": None,
    }

    with patch("app.pdf_extractor.extract_page_texts", return_value=fake_page_texts), \
         patch("app.pdf_extractor.vision_pass", new_callable=AsyncMock, return_value={2: "KCALS 267 P 37g"}), \
         patch("app.pdf_extractor.detect_recipe_boundaries", new_callable=AsyncMock, return_value=fake_boundaries), \
         patch("app.pdf_extractor.sample_recipe_indices", return_value=[0]), \
         patch("app.extractor.extract_recipe", new_callable=AsyncMock, return_value=fake_extracted):

        session = await create_session("/fake/book.pdf", "My Book", "my-book", session_id="fixed-id")

    assert session.session_id == "fixed-id"
    assert session.extraction_complete is True
    assert "0" in session.extracted
    assert session.extracted["0"]["dish_name"] == "Brownie"
    assert session.extracted["0"]["status"] == "pending"
    assert session.extracted["0"]["source_url"] == "pdf:my-book#brownie"
    # Session file persisted
    assert (tmp_path / "fixed-id.json").exists()
