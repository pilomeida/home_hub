import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.main import app
from app.database import get_session
from app.models import Recipe


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


def _seed_recipes(client):
    """Add sample recipes to the test DB."""
    session = next(client.app.dependency_overrides[get_session]())
    recipes = [
        Recipe(title="Bolo de Chocolate (whey)", dish_name="Bolo de Chocolate",
               distinguisher="whey", type="sweet", subtype="dessert",
               calories_per_portion=350, calorie_tier="mid",
               macro_tags=json.dumps(["protein-rich"]),
               ingredients=json.dumps(["egg", "flour", "chocolate", "whey"]),
               prep_time=10, cook_time=30, total_time=40,
               source_url="https://ig.com/1"),
        Recipe(title="Frango Grelhado (keto)", dish_name="Frango Grelhado",
               distinguisher="keto", type="savory", subtype="main",
               calories_per_portion=500, calorie_tier="mid",
               macro_tags=json.dumps(["protein-rich", "keto"]),
               ingredients=json.dumps(["chicken", "garlic", "olive oil"]),
               prep_time=10, cook_time=20, total_time=30,
               source_url="https://ig.com/2"),
        Recipe(title="Salada Caesar (low-cal)", dish_name="Salada Caesar",
               distinguisher="low-cal", type="savory", subtype="salad",
               calories_per_portion=180, calorie_tier="low",
               macro_tags=json.dumps(["fiber-rich"]),
               ingredients=json.dumps(["lettuce", "chicken", "parmesan"]),
               prep_time=15, cook_time=None, total_time=15,
               source_url="https://ig.com/3"),
    ]
    session.add_all(recipes)
    session.commit()


class TestBrowsePage:
    def test_shows_all_recipes(self, client):
        _seed_recipes(client)
        response = client.get("/")
        assert response.status_code == 200
        assert "Bolo de Chocolate" in response.text
        assert "Frango Grelhado" in response.text
        assert "Salada Caesar" in response.text

    def test_filter_by_type(self, client):
        _seed_recipes(client)
        response = client.get("/?type=sweet")
        assert "Bolo de Chocolate" in response.text
        assert "Frango Grelhado" not in response.text
        assert "Salada Caesar" not in response.text

    def test_filter_by_calorie_tier(self, client):
        _seed_recipes(client)
        response = client.get("/?calorie_tier=low")
        assert "Salada Caesar" in response.text
        assert "Bolo de Chocolate" not in response.text

    def test_filter_by_subtype(self, client):
        _seed_recipes(client)
        response = client.get("/?subtype=main&subtype=salad")
        assert "Frango Grelhado" in response.text
        assert "Salada Caesar" in response.text
        assert "Bolo de Chocolate" not in response.text

    def test_filter_by_macro(self, client):
        _seed_recipes(client)
        response = client.get("/?macro=protein-rich")
        assert "Bolo de Chocolate" in response.text
        assert "Frango Grelhado" in response.text
        assert "Salada Caesar" not in response.text

    def test_filter_by_ingredient(self, client):
        _seed_recipes(client)
        response = client.get("/?ingredient=chicken")
        assert "Frango Grelhado" in response.text
        assert "Salada Caesar" in response.text
        assert "Bolo de Chocolate" not in response.text

    def test_filter_by_max_time(self, client):
        _seed_recipes(client)
        response = client.get("/?max_time=20")
        assert "Salada Caesar" in response.text     # 15 min
        assert "Frango Grelhado" not in response.text  # 30 min
        assert "Bolo de Chocolate" not in response.text  # 40 min

    def test_filter_by_min_rating(self, client):
        _seed_recipes(client)
        # Set rating on one recipe
        session = next(client.app.dependency_overrides[get_session]())
        recipe = session.query(Recipe).filter_by(dish_name="Bolo de Chocolate").first()
        recipe.rating = 4
        session.commit()

        response = client.get("/?min_rating=4")
        assert "Bolo de Chocolate" in response.text
        assert "Frango Grelhado" not in response.text

    def test_combined_filters(self, client):
        _seed_recipes(client)
        response = client.get("/?type=savory&calorie_tier=low")
        assert "Salada Caesar" in response.text
        assert "Frango Grelhado" not in response.text

    def test_sidebar_includes_available_filters(self, client):
        _seed_recipes(client)
        response = client.get("/")
        # Available types
        assert "sweet" in response.text.lower()
        assert "savory" in response.text.lower()
        # Available ingredients
        assert "chicken" in response.text.lower()
        assert "chocolate" in response.text.lower()
