import pytest
from unittest.mock import MagicMock, patch

from app.scraper import ScrapedContent, ScrapeError, fetch_content


class TestFetchContent:
    def test_routes_all_urls_to_generic(self):
        """fetch_content always uses the generic HTTP scraper."""
        with patch("app.scraper._fetch_generic", MagicMock()) as mock_gen:
            mock_gen.return_value = ScrapedContent(
                text="recipe text", image_path=None,
                source_url="https://example.com/recipe",
            )
            result = fetch_content("https://example.com/recipe")

        mock_gen.assert_called_once()
        assert result.text == "recipe text"

    def test_generic_scraper_raises_on_failure(self):
        """Generic scraper raises ScrapeError on HTTP errors."""
        with patch("app.scraper._fetch_generic", MagicMock()) as mock_gen:
            mock_gen.side_effect = ScrapeError("HTTP 404")
            with pytest.raises(ScrapeError, match="HTTP 404"):
                fetch_content("https://broken.link/recipe")


class TestScrapedContent:
    def test_dataclass_fields(self):
        content = ScrapedContent(
            text="some text",
            image_path="/path/to/photo.jpg",
            source_url="https://example.com",
        )
        assert content.text == "some text"
        assert content.image_path == "/path/to/photo.jpg"

    def test_no_image(self):
        content = ScrapedContent(
            text="text only",
            image_path=None,
            source_url="https://example.com",
        )
        assert content.image_path is None
