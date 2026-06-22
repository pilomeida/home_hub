import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.main import app
from app.database import get_session


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


class TestRoutes:
    def test_browse_page_returns_200(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_add_page_returns_200(self, client):
        response = client.get("/add")
        assert response.status_code == 200

    def test_detail_page_404_for_missing(self, client):
        response = client.get("/recipe/999")
        assert response.status_code == 404

    def test_edit_page_404_for_missing(self, client):
        response = client.get("/edit/999")
        assert response.status_code == 404

    def test_detail_page_shows_recipe(self, client):
        from app.models import Recipe

        # Use the overridden session
        session = next(client.app.dependency_overrides[get_session]())
        recipe = Recipe(
            title="Test Recipe", dish_name="Test", type="sweet",
            source_url="https://example.com/test-route",
        )
        session.add(recipe)
        session.commit()
        session.refresh(recipe)

        response = client.get(f"/recipe/{recipe.id}")
        assert response.status_code == 200
        assert "Test Recipe" in response.text
