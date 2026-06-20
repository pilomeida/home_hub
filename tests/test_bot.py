import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.bot import is_url, process_extraction


class TestIsUrl:
    def test_instagram_url(self):
        assert is_url("https://www.instagram.com/p/abc123/") is True

    def test_instagram_reel_url(self):
        assert is_url("https://www.instagram.com/reel/xyz789/") is True

    def test_generic_url(self):
        assert is_url("https://example.com/recipe") is True

    def test_not_a_url(self):
        assert is_url("hello world") is False
        assert is_url("not a url at all") is False

    def test_empty_string(self):
        assert is_url("") is False


class TestProcessExtraction:
    @pytest.mark.asyncio
    async def test_successful_extraction(self, db_session):
        def _yield_session():
            yield db_session

        with patch("app.bot.fetch_content") as mock_fetch, \
             patch("app.bot.extract_recipe", AsyncMock()) as mock_extract, \
             patch("app.bot.detect_and_link") as mock_link, \
             patch("app.bot.get_session", side_effect=_yield_session):
            mock_fetch.return_value = MagicMock(
                text="recipe text",
                image_path="/tmp/photo.jpg",
                source_url="https://ig.com/p/test",
            )
            mock_extract.return_value = {
                "dish_name": "Bolo de Chocolate",
                "distinguishing_feature": "whey protein",
                "type": "sweet",
                "subtype": "dessert",
                "macro_tags": ["protein-rich"],
                "calories_per_portion": 350,
                "ingredients": ["egg", "flour", "chocolate"],
                "prep_time_minutes": 10,
                "cook_time_minutes": 30,
                "portions": 8,
                "instructions": "Mix and bake",
                "missing_critical_info": False,
            }
            mock_link.return_value = 1

            with patch("app.main.url_for") as mock_url:
                mock_url.return_value = "http://1.2.3.4/recipe/1"
                result = await process_extraction("https://ig.com/p/test")

        assert result is not None
        assert result["title"] == "Bolo de Chocolate (whey protein)"
        assert result["calorie_tier"] == "mid"
        assert result["id"] == 1

    @pytest.mark.asyncio
    async def test_scrape_failure_returns_none(self):
        with patch("app.bot.fetch_content") as mock_fetch:
            mock_fetch.side_effect = Exception("Scrape failed")
            result = await process_extraction("https://broken.link")

        assert result is None

    @pytest.mark.asyncio
    async def test_extraction_failure_returns_none(self, db_session):
        def _yield_session():
            yield db_session

        with patch("app.bot.fetch_content") as mock_fetch, \
             patch("app.bot.extract_recipe", AsyncMock()) as mock_extract, \
             patch("app.bot.get_session", side_effect=_yield_session):
            mock_fetch.return_value = MagicMock(
                text="some text", image_path=None,
                source_url="https://example.com",
            )
            from app.extractor import ExtractionError
            mock_extract.side_effect = ExtractionError("LLM failed")

            result = await process_extraction("https://example.com/recipe")

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_extraction_text_returns_none(self, db_session):
        """process_extraction raises ExtractionError on empty scraper text."""
        def _yield_session():
            yield db_session

        with patch("app.bot.fetch_content") as mock_fetch, \
             patch("app.bot.extract_recipe", AsyncMock()) as mock_extract, \
             patch("app.bot.get_session", side_effect=_yield_session):
            mock_fetch.return_value = MagicMock(
                text="", image_path=None, source_url="https://broken.link",
            )
            from app.extractor import ExtractionError
            mock_extract.side_effect = ExtractionError("Empty text")

            result = await process_extraction("https://broken.link")
            assert result is None
