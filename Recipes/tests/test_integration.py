"""Integration tests: full pipeline from URL to DB entry."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.main import app
from app.database import get_session
from app.models import Recipe, CrossLink


@pytest.fixture(autouse=True)
def _disable_bot(monkeypatch):
    """Prevent the Telegram bot from starting during tests."""
    import app.main as am
    monkeypatch.setattr(am, "start_bot", None)


@pytest.fixture
def client():
    """FastAPI TestClient with in-memory SQLite via StaticPool."""
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


class TestBotIntegration:
    """Test the full bot ingestion pipeline end-to-end."""

    @pytest.mark.asyncio
    async def test_full_pipeline_creates_recipe_and_links(self):
        """process_extraction should fetch, extract, store, and return recipe data."""
        from app.bot import process_extraction

        # Use a dedicated in-memory DB for the bot's internal get_session() calls
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)

        def _yield_session():
            with Session(engine) as s:
                yield s

        # Mock scraper
        with patch("app.bot.fetch_content") as mock_fetch:
            mock_fetch.return_value = MagicMock(
                text="Bolo de Chocolate proteico. Ingredientes: ovos, farinha, whey, chocolate.",
                image_path="/tmp/test.jpg",
                source_url="https://ig.com/integration-test-1",
            )

            # Mock extractor
            with patch("app.bot.extract_recipe", new_callable=AsyncMock) as mock_extract:
                mock_extract.return_value = {
                    "dish_name": "Bolo de Chocolate",
                    "distinguishing_feature": "whey protein",
                    "type": "sweet",
                    "subtype": "dessert",
                    "macro_tags": ["protein-rich"],
                    "calories_per_portion": 400,
                    "ingredients": ["egg", "flour", "whey", "chocolate"],
                    "prep_time_minutes": 10,
                    "cook_time_minutes": 30,
                    "portions": 8,
                    "instructions": "Mix and bake",
                    "missing_critical_info": False,
                }

                # Mock cross-link detector
                with patch("app.bot.detect_and_link") as mock_link:
                    mock_link.return_value = 0

                    # Provide our in-memory session to the bot pipeline
                    with patch("app.bot.get_session", side_effect=_yield_session):
                        with patch("app.main.url_for") as mock_url:
                            mock_url.return_value = "http://1.2.3.4/recipe/1"

                            result = await process_extraction(
                                "https://ig.com/integration-test-1"
                            )

        assert result is not None
        assert result["title"] == "Bolo de Chocolate (whey protein)"
        assert result["calorie_tier"] == "mid"
        assert result["id"] == 1

        # Verify recipe was persisted to the database
        with Session(engine) as session:
            recipe = session.exec(
                select(Recipe).where(
                    Recipe.source_url == "https://ig.com/integration-test-1"
                )
            ).first()
            assert recipe is not None
            assert recipe.dish_name == "Bolo de Chocolate"
            assert recipe.type == "sweet"
            assert recipe.calorie_tier == "mid"

    @pytest.mark.asyncio
    async def test_bot_pipeline_scrape_failure_returns_none(self):
        """process_extraction returns None when scraping fails."""
        from app.bot import process_extraction

        with patch("app.bot.fetch_content") as mock_fetch:
            mock_fetch.side_effect = Exception("Scrape failed")

            result = await process_extraction("https://broken.link")

        assert result is None

    def test_duplicate_url_detected_at_add_page(self, client):
        """Submitting a duplicate URL on /add returns error page, not duplicate."""
        # Insert first recipe via the add form
        client.post("/add", data={
            "title": "Exists", "dish_name": "Exists", "type": "sweet",
            "source_url": "https://example.com/duplicate-test",
        })

        # Try to add the same URL again
        response = client.post("/add", data={
            "title": "Duplicate", "dish_name": "Dup", "type": "sweet",
            "source_url": "https://example.com/duplicate-test",
        })
        assert response.status_code == 200
        assert "already exists" in response.text.lower()

    def test_duplicate_url_still_accessible(self, client):
        """The original recipe remains accessible after duplicate rejection."""
        client.post("/add", data={
            "title": "Original", "dish_name": "Original", "type": "savory",
            "source_url": "https://example.com/dup-still-there",
        })

        # Verify it exists via browse
        response = client.get("/")
        assert response.status_code == 200
        assert "Original" in response.text


class TestCrossLinkIntegration:
    """Test cross-links end-to-end from add to browse."""

    def test_similar_recipes_linked_on_add(self, client):
        """Adding two recipes with overlapping ingredients creates auto cross-links."""
        # Insert first recipe
        client.post("/add", data={
            "title": "Bolo Choc 1", "dish_name": "Bolo de Chocolate",
            "type": "sweet", "subtype": "dessert",
            "ingredients": '["egg","flour","chocolate","butter"]',
            "source_url": "https://ig.com/link-test-1",
        })
        # Insert second recipe with overlapping ingredients
        client.post("/add", data={
            "title": "Bolo Choc 2", "dish_name": "Bolo de Chocolate Fit",
            "type": "sweet", "subtype": "dessert",
            "ingredients": '["egg","flour","chocolate","whey"]',
            "source_url": "https://ig.com/link-test-2",
        })

        # Check cross-links were created
        session = next(client.app.dependency_overrides[get_session]())
        links = session.exec(select(CrossLink)).all()
        assert len(links) >= 1
        assert links[0].auto_generated is True

    def test_no_links_for_different_types(self, client):
        """Recipes of different types (sweet vs savory) should not be linked."""
        client.post("/add", data={
            "title": "Sweet Dish", "dish_name": "Chocolate Cake",
            "type": "sweet", "subtype": "dessert",
            "ingredients": '["egg","flour","chocolate","sugar"]',
            "source_url": "https://ig.com/no-link-1",
        })
        client.post("/add", data={
            "title": "Savory Dish", "dish_name": "Chocolate Omelette",
            "type": "savory", "subtype": "main",
            "ingredients": '["egg","flour","chocolate","salt"]',
            "source_url": "https://ig.com/no-link-2",
        })

        session = next(client.app.dependency_overrides[get_session]())
        links = session.exec(select(CrossLink)).all()
        assert len(links) == 0

    def test_browse_page_shows_linked_recipes(self, client):
        """After linking, the browse page renders both recipes correctly."""
        client.post("/add", data={
            "title": "A Recipe", "dish_name": "Dish A", "type": "sweet",
            "subtype": "dessert",
            "ingredients": '["a","b","c"]',
            "source_url": "https://ig.com/browse-link-1",
        })
        client.post("/add", data={
            "title": "B Recipe", "dish_name": "Dish A Variant", "type": "sweet",
            "subtype": "dessert",
            "ingredients": '["a","b","c","d"]',
            "source_url": "https://ig.com/browse-link-2",
        })

        response = client.get("/")
        assert response.status_code == 200
        # Browse page renders card titles, not dish_names
        assert "A Recipe" in response.text
        assert "B Recipe" in response.text

    def test_detail_page_shows_linked_recipes(self, client):
        """The detail page of a linked recipe shows the linked recipe(s)."""
        r1 = client.post("/add", data={
            "title": "Linked Parent", "dish_name": "Parent Dish",
            "type": "sweet", "subtype": "dessert",
            "ingredients": '["x","y","z","extra1"]',
            "source_url": "https://ig.com/detail-link-1",
        }, follow_redirects=False)
        r2 = client.post("/add", data={
            "title": "Linked Child", "dish_name": "Parent Dish Variant",
            "type": "sweet", "subtype": "dessert",
            "ingredients": '["x","y","z","extra2"]',
            "source_url": "https://ig.com/detail-link-2",
        }, follow_redirects=False)

        # Get the recipe IDs from the redirects
        assert r1.status_code == 302
        assert r2.status_code == 302
        detail_url_1 = r1.headers["location"]
        detail_url_2 = r2.headers["location"]

        # Visit the first recipe's detail page
        response = client.get(detail_url_1)
        assert response.status_code == 200
        # The detail page renders linked recipe titles (not dish_names)
        assert "Linked Child" in response.text

        # Visit the second recipe's detail page
        response = client.get(detail_url_2)
        assert response.status_code == 200
        assert "Linked Parent" in response.text
