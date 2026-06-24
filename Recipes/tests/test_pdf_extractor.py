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
    create_session,
    get_pending_batch,
    load_session,
    save_session,
)
import app.pdf_extractor as _pe


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


from app.pdf_extractor import is_image_pdf, build_recipe_windows


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
    # A: end = min(10, 13) = 10 → window is just [10]
    assert windows[0]["window_pages"] == [10]
    # B: start = max(11, 8) = 11 → 10 not in window
    assert 10 not in windows[1]["window_pages"]
    assert 11 in windows[1]["window_pages"]


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
    # Window clamped to ±3 from card page when no neighbors
    assert min(windows[0]["window_pages"]) >= 47
    assert max(windows[0]["window_pages"]) <= 53
