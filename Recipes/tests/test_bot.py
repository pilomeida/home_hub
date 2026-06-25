import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.bot import process_extraction, _store_recipe


class TestProcessExtraction:
    @pytest.mark.asyncio
    async def test_successful_extraction(self, db_session):
        def _yield_session():
            yield db_session

        with patch("app.bot.fetch_content") as mock_fetch, \
             patch("app.bot.extract_recipe", AsyncMock()) as mock_extract, \
             patch("app.bot.detect_and_link") as mock_link, \
             patch("app.bot.get_session", side_effect=_yield_session), \
             patch("app.main.url_for", return_value="http://1.2.3.4/recipe/1"):
            mock_fetch.return_value = MagicMock(
                text="recipe text",
                image_path="/tmp/photo.jpg",
                source_url="https://example.com/recipe",
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
                "cooking_types": ["oven"],
                "protein_g": 20,
                "fat_g": 10,
                "carbs_g": 40,
                "fiber_g": None,
            }
            mock_link.return_value = 1

            result = await process_extraction("https://example.com/recipe")

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
    async def test_extraction_failure_returns_none(self):
        with patch("app.bot.fetch_content") as mock_fetch, \
             patch("app.bot.extract_recipe", AsyncMock()) as mock_extract:
            mock_fetch.return_value = MagicMock(
                text="some text", image_path=None,
                source_url="https://example.com",
            )
            from app.extractor import ExtractionError
            mock_extract.side_effect = ExtractionError("LLM failed")

            result = await process_extraction("https://example.com/recipe")

        assert result is None


class TestStoreRecipe:
    @pytest.mark.asyncio
    async def test_stores_recipe_and_returns_summary(self, db_session):
        def _yield_session():
            yield db_session

        data = {
            "dish_name": "Avocado Toast",
            "distinguishing_feature": None,
            "type": "savory",
            "subtype": "breakfast",
            "macro_tags": ["vegan"],
            "calories_per_portion": 280,
            "ingredients": ["bread", "avocado"],
            "prep_time_minutes": 5,
            "cook_time_minutes": None,
            "portions": 1,
            "instructions": "Toast bread. Mash avocado.",
            "missing_critical_info": False,
            "cooking_types": ["no-cook"],
            "protein_g": 6,
            "fat_g": 14,
            "carbs_g": 28,
            "fiber_g": 7,
        }

        with patch("app.bot.get_session", side_effect=_yield_session), \
             patch("app.bot.detect_and_link"), \
             patch("app.main.url_for", return_value="http://1.2.3.4/recipe/1"):
            result = await _store_recipe(data, None, "tg://img/test123")

        assert result["title"] == "Avocado Toast"
        assert result["calorie_tier"] == "low"

    @pytest.mark.asyncio
    async def test_image_recipe_sets_source_title(self, db_session):
        def _yield_session():
            yield db_session

        data = {
            "dish_name": "Pancakes",
            "distinguishing_feature": None,
            "type": "sweet",
            "subtype": "breakfast",
            "macro_tags": [],
            "calories_per_portion": None,
            "ingredients": ["flour", "egg", "milk"],
            "prep_time_minutes": 5,
            "cook_time_minutes": 10,
            "portions": 2,
            "instructions": "Mix and cook.",
            "missing_critical_info": False,
            "cooking_types": ["cooktop"],
            "protein_g": None,
            "fat_g": None,
            "carbs_g": None,
            "fiber_g": None,
        }

        with patch("app.bot.get_session", side_effect=_yield_session), \
             patch("app.bot.detect_and_link"), \
             patch("app.main.url_for", return_value="http://1.2.3.4/recipe/2"):
            result = await _store_recipe(data, "/tmp/food.jpg", "tg://img/abc456")

        assert result is not None
        # Verify source_title was set to "Telegram" by checking DB
        recipe = db_session.query(__import__("app.models", fromlist=["Recipe"]).Recipe)\
            .filter_by(source_url="tg://img/abc456").first()
        assert recipe.source_title == "Social Networks"


class TestPhotoGroupHandler:
    @pytest.mark.asyncio
    async def test_single_photo_rejected(self):
        """A single photo triggers a help message."""
        from app.bot import _process_group_after_delay
        import app.bot as bot_module

        group_id = "single_test"
        fake_photo = MagicMock()
        fake_photo.file_id = "file_abc"
        bot_module._media_group_buffers[group_id] = [fake_photo]

        fake_update = MagicMock()
        fake_update.message.reply_text = AsyncMock()
        bot_module._media_group_updates[group_id] = fake_update

        context = MagicMock()

        # Patch sleep so the delay doesn't actually wait
        with patch("asyncio.sleep", AsyncMock()):
            await _process_group_after_delay(context, group_id, delay=0)

        fake_update.message.reply_text.assert_called_once()
        call_text = fake_update.message.reply_text.call_args[0][0]
        assert "2 photos" in call_text or "at least" in call_text.lower()

    @pytest.mark.asyncio
    async def test_two_photos_triggers_extraction(self, db_session):
        """Two photos: first = recipe screenshot, last = food photo → recipe saved."""
        from app.bot import _process_group_after_delay
        import app.bot as bot_module

        group_id = "album_test"
        fake_screenshot = MagicMock()
        fake_screenshot.file_id = "file_screenshot"
        fake_food = MagicMock()
        fake_food.file_id = "file_food"
        bot_module._media_group_buffers[group_id] = [fake_screenshot, fake_food]

        fake_update = MagicMock()
        fake_update.message.reply_text = AsyncMock()
        bot_module._media_group_updates[group_id] = fake_update

        screenshot_bytes = b"screenshot_data"
        food_bytes = b"food_photo_data"

        async def fake_get_file(file_id):
            tg_file = MagicMock()
            tg_file.download_as_bytearray = AsyncMock(
                return_value=bytearray(
                    screenshot_bytes if file_id == "file_screenshot" else food_bytes
                )
            )
            return tg_file

        context = MagicMock()
        context.bot.get_file = AsyncMock(side_effect=fake_get_file)

        extracted_data = {
            "dish_name": "Smoothie Bowl",
            "distinguishing_feature": None,
            "type": "sweet",
            "subtype": "breakfast",
            "macro_tags": ["vegan"],
            "calories_per_portion": 320,
            "ingredients": ["banana", "berries"],
            "prep_time_minutes": 5,
            "cook_time_minutes": None,
            "portions": 1,
            "instructions": "Blend and pour.",
            "missing_critical_info": False,
            "cooking_types": ["blender"],
            "protein_g": 8,
            "fat_g": 5,
            "carbs_g": 60,
            "fiber_g": 8,
        }

        def _yield_session():
            yield db_session

        with patch("asyncio.sleep", AsyncMock()), \
             patch("app.bot.extract_recipe_from_images", AsyncMock(return_value=extracted_data)), \
             patch("app.bot.get_session", side_effect=_yield_session), \
             patch("app.bot.detect_and_link"), \
             patch("app.main.url_for", return_value="http://1.2.3.4/recipe/1"), \
             patch("pathlib.Path.write_bytes"):
            await _process_group_after_delay(context, group_id, delay=0)

        # Should have replied twice: acknowledgement + success
        assert fake_update.message.reply_text.call_count == 2
        success_text = fake_update.message.reply_text.call_args[0][0]
        assert "Smoothie Bowl" in success_text
