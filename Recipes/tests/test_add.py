import json
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool
from unittest.mock import patch, MagicMock, AsyncMock

from app.main import app
from app.database import get_session
from app.models import Recipe


@pytest.fixture(autouse=True)
def _disable_bot(monkeypatch):
    """Prevent the Telegram bot from starting during tests."""
    import app.main
    monkeypatch.setattr(app.main, "start_bot", None)


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestAddPage:
    def test_get_add_page(self, client):
        response = client.get("/add")
        assert response.status_code == 200
        assert "Add Recipe" in response.text

    def test_manual_save(self, client):
        form_data = {
            "title": "Panqueca Proteica",
            "dish_name": "Panqueca",
            "distinguisher": "proteica",
            "type": "sweet",
            "subtype": "breakfast",
            "calories_per_portion": "300",
            "macro_tags": '["protein-rich"]',
            "ingredients": '["egg", "whey", "banana"]',
            "prep_time": "5",
            "cook_time": "10",
            "portions": "2",
            "instructions": "Mix and cook",
            "source_url": "https://example.com/panqueca",
        }
        response = client.post("/add", data=form_data, follow_redirects=False)
        assert response.status_code == 302  # redirect to detail

        # Verify in DB
        session = next(client.app.dependency_overrides[get_session]())
        recipe = session.exec(select(Recipe).where(Recipe.source_url == "https://example.com/panqueca")).first()
        assert recipe is not None
        assert recipe.title == "Panqueca Proteica"
        assert recipe.total_time == 15
        assert recipe.calorie_tier == "mid"

    def test_duplicate_url_rejected(self, client):
        # Insert first
        client.post("/add", data={
            "title": "Test", "dish_name": "Test", "type": "sweet",
            "source_url": "https://example.com/dup",
        })
        # Try duplicate
        response = client.post("/add", data={
            "title": "Test 2", "dish_name": "Test", "type": "sweet",
            "source_url": "https://example.com/dup",
        })
        assert response.status_code == 200  # stays on add page with error
        assert "already exists" in response.text.lower()


class TestEditPage:
    def test_get_edit_page(self, client):
        session = next(client.app.dependency_overrides[get_session]())
        r = Recipe(title="Edit Me", dish_name="Edit", type="sweet", source_url="https://ig.com/edit1")
        session.add(r)
        session.commit()
        session.refresh(r)

        response = client.get(f"/edit/{r.id}")
        assert response.status_code == 200
        assert "Edit Me" in response.text

    def test_edit_save(self, client):
        session = next(client.app.dependency_overrides[get_session]())
        r = Recipe(title="Original", dish_name="Original", type="sweet",
                    source_url="https://ig.com/edit2")
        session.add(r)
        session.commit()
        session.refresh(r)

        form_data = {
            "title": "Updated Title",
            "dish_name": "Original Updated",
            "type": "savory",
            "source_url": "https://ig.com/edit2",
        }
        response = client.post(f"/edit/{r.id}", data=form_data, follow_redirects=False)
        assert response.status_code == 302

        session.refresh(r)
        assert r.title == "Updated Title"
        assert r.type == "savory"

    def test_url_fetch_endpoint(self, client):
        """POST /add/fetch returns extracted data as JSON (non-committal)."""
        with patch("app.routes_add.fetch_content") as mock_fetch:
            mock_fetch.return_value = MagicMock(
                text="recipe text", image_path=None,
                source_url="https://example.com/fetch",
            )
            with patch("app.routes_add.extract_recipe", AsyncMock()) as mock_extract:
                mock_extract.return_value = {
                    "dish_name": "Fetched Dish",
                    "distinguishing_feature": "test",
                    "type": "sweet",
                    "subtype": "dessert",
                    "macro_tags": [],
                    "calories_per_portion": 400,
                    "ingredients": ["a", "b"],
                    "prep_time_minutes": 10,
                    "cook_time_minutes": 20,
                    "portions": 4,
                    "instructions": "Steps here",
                    "missing_critical_info": False,
                }

                response = client.post("/add/fetch", data={"url": "https://example.com/fetch"})
                assert response.status_code == 200
                data = response.json()
                assert data["dish_name"] == "Fetched Dish"
