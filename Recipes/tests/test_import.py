"""Tests for PDF import routes."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

import app.pdf_extractor as _pe
from app.main import app

client = TestClient(app)


def _seed_session(tmp_path: Path, overrides: dict | None = None) -> dict:
    """Write a fake session JSON and return its data."""
    data = {
        "session_id": "test-sid",
        "pdf_path": "/tmp/book.pdf",
        "book_title": "Test Book",
        "book_slug": "test-book",
        "all_recipes": [
            {"recipe_title": "Brownie", "pages": [1], "status": "pending"},
        ],
        "sampled_indices": [0],
        "extracted": {
            "0": {
                "dish_name": "Brownie", "distinguishing_feature": None,
                "type": "sweet", "subtype": "dessert",
                "macro_tags": [], "calories_per_portion": 267,
                "ingredients": ["egg"], "prep_time_minutes": 2,
                "cook_time_minutes": 3, "portions": 1,
                "instructions": "Microwave.", "missing_critical_info": False,
                "cooking_types": ["microwave"], "protein_g": 37, "fat_g": 9,
                "carbs_g": 17, "fiber_g": None, "status": "pending",
                "source_url": "pdf:test-book#brownie",
                "book_title": "Test Book",
            }
        },
        "extraction_complete": True,
        "created_at": "2026-06-22T00:00:00",
        "toc_recipes": [],
        "pipeline": "text",
        "test_mode": True,
        "current_recipe": "",
        "error": None,
        "total_pages": 0,
        "used_windows": [],
        "sample_pages": [],
        "auto_approved": False,
    }
    if overrides:
        data.update(overrides)
    (tmp_path / "test-sid.json").write_text(json.dumps(data))
    return data


def test_import_upload_page_renders():
    response = client.get("/import")
    assert response.status_code == 200
    assert b"Import" in response.content


def test_status_returns_json(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_SESSIONS_DIR", tmp_path)
    _seed_session(tmp_path)
    response = client.get("/import/test-sid/status")
    assert response.status_code == 200
    data = response.json()
    assert data["extraction_complete"] is True
    assert data["book_title"] == "Test Book"
    assert data["extracted_so_far"] == 1
    assert "current_recipe" in data


def test_review_page_renders_pending_recipes(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_SESSIONS_DIR", tmp_path)
    _seed_session(tmp_path)
    response = client.get("/import/test-sid/review")
    assert response.status_code == 200
    assert b"Brownie" in response.content


def test_review_page_redirects_to_summary_when_no_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_SESSIONS_DIR", tmp_path)
    _seed_session(tmp_path, {
        "extracted": {"0": {"dish_name": "Brownie", "status": "approved"}}
    })
    response = client.get("/import/test-sid/review", follow_redirects=False)
    assert response.status_code == 303
    assert "/summary" in response.headers["location"]


def test_summary_page_renders(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_SESSIONS_DIR", tmp_path)
    _seed_session(tmp_path, {
        "extracted": {"0": {
            "dish_name": "Brownie", "status": "approved",
            "saved_recipe_id": 1,
        }}
    })
    response = client.get("/import/test-sid/summary")
    assert response.status_code == 200
    assert b"Brownie" in response.content


def test_unknown_session_returns_404(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_SESSIONS_DIR", tmp_path)
    response = client.get("/import/no-such-session/status")
    assert response.status_code == 404


def test_submit_review_saves_recipe_to_db(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_SESSIONS_DIR", tmp_path)
    _seed_session(tmp_path)
    form_data = {
        "action_0": "save",
        "dish_name_0": "Brownie",
        "distinguisher_0": "",
        "type_0": "sweet",
        "subtype_0": "dessert",
        "calories_0": "267",
        "portions_0": "1",
        "protein_g_0": "37",
        "fat_g_0": "9",
        "carbs_g_0": "17",
        "fiber_g_0": "",
    }
    response = client.post("/import/test-sid/review", data=form_data, follow_redirects=False)
    assert response.status_code == 303

    # Verify recipe was saved and session updated
    import json as _json
    session_data = _json.loads((tmp_path / "test-sid.json").read_text())
    assert session_data["extracted"]["0"]["status"] == "approved"
    assert "saved_recipe_id" in session_data["extracted"]["0"]


def test_clear_recipes_deletes_by_title(tmp_path, monkeypatch):
    from app.database import engine
    from sqlmodel import Session as DBSession
    from app.models import Recipe

    # Use a unique slug to avoid UNIQUE constraint conflicts with other tests
    test_url = "pdf:clear-test-book#chocolate-cake"

    # Clean up any leftovers from previous runs
    with DBSession(engine) as db:
        existing = db.exec(select(Recipe).where(Recipe.source_url == test_url)).first()
        if existing:
            db.delete(existing)
            db.commit()

    # Seed a recipe that looks like it came from "Clear Test Book"
    with DBSession(engine) as db:
        r = Recipe(
            title="Chocolate Cake", dish_name="Chocolate Cake", type="sweet",
            source_url=test_url,
            source_title="Clear Test Book",
        )
        db.add(r)
        db.commit()

    response = client.post("/import/clear", data={
        "book_title": "Clear Test Book",
        "book_slug": "clear-test-book",
    })
    assert response.status_code == 200
    assert response.json()["deleted"] == 1

    # Verify deleted from DB
    with DBSession(engine) as db:
        result = db.exec(
            select(Recipe).where(Recipe.source_url == test_url)
        ).first()
    assert result is None


def test_clear_recipes_requires_book_slug():
    response = client.post("/import/clear", data={"book_title": "X"})
    assert response.status_code == 400


def test_bulk_page_renders_pending_recipes(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_SESSIONS_DIR", tmp_path)
    _seed_session(tmp_path, {"test_mode": False, "pipeline": "vision"})
    response = client.get("/import/test-sid/bulk")
    assert response.status_code == 200
    assert b"Brownie" in response.content


def test_submit_bulk_saves_recipes(tmp_path, monkeypatch):
    monkeypatch.setattr(_pe, "_SESSIONS_DIR", tmp_path)
    _seed_session(tmp_path, {"test_mode": False, "pipeline": "vision"})
    response = client.post("/import/test-sid/bulk", data={}, follow_redirects=False)
    assert response.status_code == 303
    assert "/summary" in response.headers["location"]
