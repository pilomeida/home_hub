import pytest
from unittest.mock import MagicMock, patch

from app.scraper import ScrapedContent, ScrapeError, fetch_content


class TestFetchContent:
    def test_routes_instagram_urls(self):
        """Instagram URLs are routed to instaloader path."""
        with patch("app.scraper._fetch_instagram", MagicMock()) as mock_ig:
            mock_ig.return_value = ScrapedContent(
                text="caption", image_path="/tmp/img.jpg",
                source_url="https://www.instagram.com/p/abc123/",
            )
            with patch("app.scraper._fetch_generic", MagicMock()) as mock_gen:
                result = fetch_content("https://www.instagram.com/p/abc123/")

        mock_ig.assert_called_once()
        mock_gen.assert_not_called()
        assert result.text == "caption"

    def test_routes_instagram_reel_urls(self):
        """Instagram reel URLs are also routed to instaloader."""
        with patch("app.scraper._fetch_instagram", MagicMock()) as mock_ig:
            mock_ig.return_value = ScrapedContent(
                text="reel caption", image_path=None,
                source_url="https://www.instagram.com/reel/xyz789/",
            )
            result = fetch_content("https://www.instagram.com/reel/xyz789/")

        assert result.text == "reel caption"

    def test_routes_generic_urls(self):
        """Non-Instagram URLs go to the generic fetcher."""
        with patch("app.scraper._fetch_generic", MagicMock()) as mock_gen:
            mock_gen.return_value = ScrapedContent(
                text="recipe text", image_path=None,
                source_url="https://example.com/recipe",
            )
            with patch("app.scraper._fetch_instagram", MagicMock()) as mock_ig:
                result = fetch_content("https://example.com/recipe")

        mock_gen.assert_called_once()
        mock_ig.assert_not_called()
        assert result.text == "recipe text"

    def test_generic_scraper_raises_on_failure(self):
        """Generic scraper raises ScrapeError on HTTP errors."""
        with patch("app.scraper._fetch_generic", MagicMock()) as mock_gen:
            mock_gen.side_effect = ScrapeError("HTTP 404")
            with pytest.raises(ScrapeError, match="HTTP 404"):
                fetch_content("https://broken.link/recipe")

    def test_instagram_scraper_raises_on_failure(self):
        """Instagram scraper raises ScrapeError on failures."""
        with patch("app.scraper._fetch_instagram", MagicMock()) as mock_ig:
            mock_ig.side_effect = ScrapeError("Private account")
            with pytest.raises(ScrapeError, match="Private account"):
                fetch_content("https://www.instagram.com/p/private/")


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
