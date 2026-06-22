import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.main import app
from app.database import get_session
from app.models import Recipe, CrossLink


@pytest.fixture
def client():
    """Test client with an in-memory DB."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed(client):
    """Seed two recipes and a cross-link. Returns (r1_id, r2_id)."""
    session = next(client.app.dependency_overrides[get_session]())
    r1 = Recipe(title="Bolo de Chocolate (whey)", dish_name="Bolo de Chocolate",
                distinguisher="whey", type="sweet", subtype="dessert",
                calories_per_portion=350, calorie_tier="mid",
                macro_tags=json.dumps(["protein-rich", "low-carb"]),
                ingredients=json.dumps(["egg", "flour", "chocolate", "whey"]),
                prep_time=10, cook_time=30, total_time=40,
                portions=8, instructions="1. Mix\n2. Bake",
                source_url="https://ig.com/1")
    r2 = Recipe(title="Bolo de Chocolate (low-cal)", dish_name="Bolo de Chocolate",
                distinguisher="low-cal", type="sweet", subtype="dessert",
                calories_per_portion=200, calorie_tier="low",
                ingredients=json.dumps(["egg", "flour", "chocolate", "stevia"]),
                prep_time=10, cook_time=30, total_time=40,
                source_url="https://ig.com/2")
    session.add_all([r1, r2])
    session.commit()
    session.refresh(r1)
    session.refresh(r2)
    # Capture IDs while objects are still bound to the session
    r1_id = r1.id
    r2_id = r2.id
    # Add cross-link
    link = CrossLink(recipe_id=r1_id, similar_to_id=r2_id, auto_generated=True)
    session.add(link)
    session.commit()
    return r1_id, r2_id


class TestDetailPage:
    def test_shows_recipe(self, client):
        r1_id, _ = _seed(client)
        response = client.get(f"/recipe/{r1_id}")
        assert response.status_code == 200
        assert "Bolo de Chocolate" in response.text
        assert "whey" in response.text
        assert "protein-rich" in response.text

    def test_shows_cross_links(self, client):
        r1_id, r2_id = _seed(client)
        response = client.get(f"/recipe/{r1_id}")
        assert response.status_code == 200
        assert f"/recipe/{r2_id}" in response.text
        assert "low-cal" in response.text

    def test_delete_recipe(self, client):
        r1_id, _ = _seed(client)
        response = client.post(f"/recipe/{r1_id}/delete", follow_redirects=False)
        assert response.status_code == 302  # redirect to /
        # Verify deleted
        session = next(client.app.dependency_overrides[get_session]())
        assert session.get(Recipe, r1_id) is None

    def test_update_rating(self, client):
        r1_id, _ = _seed(client)
        response = client.post(f"/recipe/{r1_id}/rate", json={"rating": 4})
        assert response.status_code == 200
        session = next(client.app.dependency_overrides[get_session]())
        updated = session.get(Recipe, r1_id)
        assert updated.rating == 4
