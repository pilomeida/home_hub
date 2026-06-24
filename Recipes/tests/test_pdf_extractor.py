"""Tests for the PDF extraction pipeline."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.pdf_extractor import (
    PageText,
    PdfIngestionSession,
    _slugify,
    assemble_recipe_text,
    build_recipe_windows,
    create_session,
    create_vision_session,
    get_pending_batch,
    is_image_pdf,
    load_session,
    save_session,
    extract_toc_vision,
    extract_recipe_vision,
    _save_recipe_photo_vision,
    VISION_EXTRACTION_PROMPT,
)
import app.pdf_extractor as _pe
import random as _random


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


def test_slugify():
    assert _slugify("Protein Brownie") == "protein-brownie"
    assert _slugify("  Hello, World! ") == "hello-world"
    assert _slugify("Mug Cake – with Tofu") == "mug-cake-with-tofu"


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


def test_is_image_pdf_returns_true_for_empty_pages(tmp_path):
    import pdfplumber
    # Create a minimal PDF via a fake pdfplumber response
    fake_pages = [MagicMock(extract_text=lambda: "")]
    with patch("pdfplumber.open") as mock_open:
        mock_open.return_value.__enter__.return_value = MagicMock(pages=fake_pages)
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        result = is_image_pdf("/fake/book.pdf")
    assert result is True


def test_is_image_pdf_returns_false_for_text_pdf():
    long_text = "A" * 200
    fake_pages = [MagicMock(extract_text=lambda: long_text)]
    with patch("pdfplumber.open") as mock_open:
        mock_open.return_value.__enter__.return_value = MagicMock(pages=fake_pages)
        mock_open.return_value.__exit__ = MagicMock(return_value=False)
        result = is_image_pdf("/fake/book.pdf")
    assert result is False


def test_session_new_fields_have_defaults():
    session = _make_session()
    assert session.toc_recipes == []
    assert session.pipeline == "text"
    assert session.test_mode is False
    assert session.current_recipe == ""


def test_session_new_fields_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_SESSIONS_DIR", tmp_path)
    session = _make_session(
        toc_recipes=[{"recipe_title": "Pasta", "page": 101}],
        pipeline="vision",
        test_mode=True,
        current_recipe="Pasta",
    )
    save_session(session)
    loaded = load_session("s1")
    assert loaded.pipeline == "vision"
    assert loaded.test_mode is True
    assert loaded.toc_recipes[0]["recipe_title"] == "Pasta"
    assert loaded.current_recipe == "Pasta"


def test_build_recipe_windows_standard_pattern():
    toc = [
        {"recipe_title": "A", "page": 10},
        {"recipe_title": "B", "page": 15},
        {"recipe_title": "C", "page": 20},
    ]
    windows = build_recipe_windows(toc)
    # B: prev=10, next=20 → start=max(11, 12)=12, end=min(19, 18)=18
    b = windows[1]
    assert b["card_page"] == 15
    assert b["recipe_title"] == "B"
    assert 15 in b["window_pages"]
    assert 10 not in b["window_pages"]
    assert 20 not in b["window_pages"]


def test_build_recipe_windows_adjacent_cards():
    toc = [
        {"recipe_title": "A", "page": 10},
        {"recipe_title": "B", "page": 11},
    ]
    windows = build_recipe_windows(toc)
    # A: next=11, so end = min(10, 13) = 10; start = max(7, 7) = 7
    assert 10 in windows[0]["window_pages"]          # card page always included
    assert 11 not in windows[0]["window_pages"]      # B's card page excluded
    # B: prev=10, so start = max(11, 8) = 11
    assert 10 not in windows[1]["window_pages"]      # A's card page excluded
    assert 11 in windows[1]["window_pages"]          # B's card page always included


def test_build_recipe_windows_double_photo_before():
    """Two photo pages before a recipe card."""
    toc = [
        {"recipe_title": "A", "page": 10},
        {"recipe_title": "B", "page": 14},
    ]
    windows = build_recipe_windows(toc)
    b = windows[1]
    # B window: start=max(11, 11)=11, end=min(13+something, 17)=17 → includes 12,13,14
    assert 12 in b["window_pages"]
    assert 13 in b["window_pages"]
    assert 14 in b["window_pages"]
    assert 10 not in b["window_pages"]


def test_build_recipe_windows_single_recipe():
    toc = [{"recipe_title": "Only", "page": 50}]
    windows = build_recipe_windows(toc)
    assert windows[0]["card_page"] == 50
    assert 50 in windows[0]["window_pages"]
    # No neighbors: sentinels are max(1, 46) and 54 → window = [47..53]
    assert min(windows[0]["window_pages"]) == 47
    assert max(windows[0]["window_pages"]) == 53
    assert len(windows[0]["window_pages"]) == 7


# ── Vision extraction tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_extract_toc_vision_returns_recipe_list():
    mock_result = [
        {"recipe_title": "Brownie Batter Blended Oats", "page": 61},
        {"recipe_title": "Creamy Mushroom Pasta", "page": 101},
    ]
    fake_imgs = [Image.new("RGB", (100, 100)) for _ in range(6)]
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=json.dumps(mock_result))])
    )
    with patch("app.pdf_extractor.convert_from_path", return_value=fake_imgs), \
         patch("app.pdf_extractor._get_client", return_value=mock_client):
        result = await extract_toc_vision("/fake/book.pdf")
    assert len(result) == 2
    assert result[0]["recipe_title"] == "Brownie Batter Blended Oats"
    assert result[0]["page"] == 61


@pytest.mark.asyncio
async def test_extract_toc_vision_strips_fences():
    wrapped = '```json\n[{"recipe_title": "Test Recipe", "page": 42}]\n```'
    fake_imgs = [Image.new("RGB", (100, 100))]
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=wrapped)])
    )
    with patch("app.pdf_extractor.convert_from_path", return_value=fake_imgs), \
         patch("app.pdf_extractor._get_client", return_value=mock_client):
        result = await extract_toc_vision("/fake/book.pdf", toc_page_range=(3, 3))
    assert result[0]["recipe_title"] == "Test Recipe"
    assert result[0]["page"] == 42


@pytest.mark.asyncio
async def test_extract_recipe_vision_returns_structured_data():
    mock_result = {
        "photo_page": 60, "photo_is_inset": False,
        "dish_name": "Brownie Batter Blended Oats",
        "distinguishing_feature": None,
        "notes": "If you love brownie batter, this is for you.",
        "type": "sweet", "subtype": "breakfast",
        "macro_tags": ["vegan", "fiber-rich"],
        "cooking_types": ["blender"],
        "calories_per_portion": 558,
        "protein_g": 23, "fat_g": 81, "carbs_g": 10, "fiber_g": None,
        "portions": 1,
        "ingredients": ["1/2 cup chickpeas*", "1 ripe banana"],
        "prep_time_minutes": None, "cook_time_minutes": None,
        "instructions": "1. Blend everything.\n2. Serve.",
        "missing_critical_info": False,
    }
    fake_imgs = [Image.new("RGB", (680, 880)) for _ in range(3)]
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=json.dumps(mock_result))])
    )
    with patch("app.pdf_extractor.convert_from_path", return_value=fake_imgs), \
         patch("app.pdf_extractor._get_client", return_value=mock_client):
        result = await extract_recipe_vision(
            "/fake/book.pdf", [60, 61, 62], 61,
            "Brownie Batter Blended Oats", "broccoli-mum",
        )
    assert result["dish_name"] == "Brownie Batter Blended Oats"
    assert result["notes"] == "If you love brownie batter, this is for you."
    assert result["photo_page"] == 60
    assert result["portions"] == 1
    assert result["cooking_types"] == ["blender"]


@pytest.mark.asyncio
async def test_extract_recipe_vision_strips_fences():
    mock_result = {"dish_name": "Test", "photo_page": None, "photo_is_inset": False,
                   "distinguishing_feature": None, "notes": None, "type": "savory",
                   "subtype": None, "macro_tags": [], "cooking_types": [],
                   "calories_per_portion": None, "protein_g": None, "fat_g": None,
                   "carbs_g": None, "fiber_g": None, "portions": 1,
                   "ingredients": [], "prep_time_minutes": None,
                   "cook_time_minutes": None, "instructions": None,
                   "missing_critical_info": True}
    wrapped = f"```json\n{json.dumps(mock_result)}\n```"
    fake_imgs = [Image.new("RGB", (100, 100))]
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text=wrapped)])
    )
    with patch("app.pdf_extractor.convert_from_path", return_value=fake_imgs), \
         patch("app.pdf_extractor._get_client", return_value=mock_client):
        result = await extract_recipe_vision(
            "/fake/book.pdf", [61], 61, "Test Recipe", "test-book"
        )
    assert result["dish_name"] == "Test"


@pytest.mark.asyncio
async def test_save_recipe_photo_vision_full_page(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_PHOTOS_DIR", tmp_path)
    fake_img = Image.new("RGB", (680, 880), color=(100, 150, 50))
    with patch("app.pdf_extractor.convert_from_path", return_value=[fake_img]):
        result = await _save_recipe_photo_vision(
            "/fake/book.pdf",
            photo_page=60, photo_is_inset=False,
            card_page=61, book_slug="broccoli-mum", recipe_slug="brownie-batter",
        )
    assert result is not None
    assert (tmp_path / "pdf-broccoli-mum-brownie-batter.jpg").exists()


@pytest.mark.asyncio
async def test_save_recipe_photo_vision_inset(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_PHOTOS_DIR", tmp_path)
    fake_img = Image.new("RGB", (680, 880))
    with patch("app.pdf_extractor.convert_from_path", return_value=[fake_img]):
        result = await _save_recipe_photo_vision(
            "/fake/book.pdf",
            photo_page=None, photo_is_inset=True,
            card_page=61, book_slug="broccoli-mum", recipe_slug="brownie-batter",
        )
    assert result is not None
    assert (tmp_path / "pdf-broccoli-mum-brownie-batter.jpg").exists()


@pytest.mark.asyncio
async def test_save_recipe_photo_vision_no_photo():
    result = await _save_recipe_photo_vision(
        "/fake/book.pdf",
        photo_page=None, photo_is_inset=False,
        card_page=61, book_slug="broccoli-mum", recipe_slug="brownie-batter",
    )
    assert result is None


# ── create_vision_session tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_vision_session_test_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(_pe, "_PHOTOS_DIR", tmp_path / "photos")

    fake_toc = [{"recipe_title": f"Recipe {i}", "page": 60 + i * 2} for i in range(10)]
    fake_extraction = {
        "photo_page": None, "photo_is_inset": False,
        "dish_name": "Recipe 0", "distinguishing_feature": None,
        "notes": "A lovely intro.", "type": "savory", "subtype": "main",
        "macro_tags": [], "cooking_types": ["oven"],
        "calories_per_portion": 400, "protein_g": 20, "fat_g": 10,
        "carbs_g": 30, "fiber_g": None, "portions": 2,
        "ingredients": ["1 cup lentils"], "prep_time_minutes": 10,
        "cook_time_minutes": 20, "instructions": "Cook.", "missing_critical_info": False,
    }

    with patch("app.pdf_extractor.extract_toc_vision", new_callable=AsyncMock, return_value=fake_toc), \
         patch("app.pdf_extractor.extract_recipe_vision", new_callable=AsyncMock, return_value=fake_extraction), \
         patch("app.pdf_extractor._save_recipe_photo_vision", new_callable=AsyncMock, return_value=None), \
         patch("app.pdf_extractor.random") as mock_random:
        mock_random.sample.return_value = [0, 2, 4, 6, 8]
        session = await create_vision_session(
            "/fake/book.pdf", "My Book", "my-book",
            n_sample=5, test_mode=True, session_id="vis-test",
        )

    assert session.session_id == "vis-test"
    assert session.pipeline == "vision"
    assert session.test_mode is True
    assert len(session.toc_recipes) == 10
    assert session.extraction_complete is True
    assert len(session.sampled_indices) == 5
    assert "0" in session.extracted
    assert session.extracted["0"]["notes"] == "A lovely intro."
    assert session.extracted["0"]["status"] == "pending"
    assert session.extracted["0"]["source_url"] == "pdf:my-book#recipe-0"
    assert (tmp_path / "vis-test.json").exists()


@pytest.mark.asyncio
async def test_create_vision_session_full_book_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(_pe, "_PHOTOS_DIR", tmp_path / "photos")

    fake_toc = [{"recipe_title": f"R{i}", "page": 10 + i * 2} for i in range(3)]
    fake_extraction = {
        "photo_page": None, "photo_is_inset": False,
        "dish_name": "R0", "distinguishing_feature": None, "notes": None,
        "type": "savory", "subtype": None, "macro_tags": [], "cooking_types": [],
        "calories_per_portion": None, "protein_g": None, "fat_g": None,
        "carbs_g": None, "fiber_g": None, "portions": 1,
        "ingredients": [], "prep_time_minutes": None,
        "cook_time_minutes": None, "instructions": None, "missing_critical_info": True,
    }

    with patch("app.pdf_extractor.extract_toc_vision", new_callable=AsyncMock, return_value=fake_toc), \
         patch("app.pdf_extractor.extract_recipe_vision", new_callable=AsyncMock, return_value=fake_extraction), \
         patch("app.pdf_extractor._save_recipe_photo_vision", new_callable=AsyncMock, return_value=None):
        session = await create_vision_session(
            "/fake/book.pdf", "Full Book", "full-book",
            n_sample=5, test_mode=False, session_id="vis-full",
        )

    # Full book: all 3 recipes extracted
    assert len(session.sampled_indices) == 3
    assert len(session.extracted) == 3
    assert session.extraction_complete is True
