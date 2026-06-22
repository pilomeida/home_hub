import json
from unittest.mock import AsyncMock, patch

import pytest

from app.extractor import ExtractionError, extract_recipe


class TestExtractRecipe:
    """Tests that the extractor parses LLM responses correctly.

    These mock the Anthropic API to test our parsing logic.
    """

    FAKE_CAPTION = """
    BOLO DE CHOCOLATE PROTEICO 🍫
    Ingredientes:
    - 3 ovos
    - 1 xícara de farinha de aveia
    - 2 scoops de whey protein chocolate
    - 1/2 xícara de cacau em pó
    - 1 colher de fermento
    Modo de preparo: mistura tudo, assa 30min a 180C.
    Rende 8 porções. Prep: 10min.
    """

    def make_mock_response(self, text_content: str):
        """Helper to create a mock Anthropic message with given text."""
        return type("MockMessage", (), {
            "content": [type("MockBlock", (), {"text": text_content})()]
        })

    @pytest.mark.asyncio
    async def test_extracts_full_recipe(self):
        response_json = {
            "dish_name": "Bolo de Chocolate",
            "distinguishing_feature": "whey protein",
            "type": "sweet",
            "subtype": "dessert",
            "macro_tags": ["protein-rich", "low-carb"],
            "calories_per_portion": 350,
            "ingredients": ["egg", "oat flour", "whey protein", "cocoa powder", "baking powder"],
            "prep_time_minutes": 10,
            "cook_time_minutes": 30,
            "portions": 8,
            "instructions": "1. Mix all ingredients\n2. Bake at 180C for 30min\n3. Serve",
            "missing_critical_info": False,
        }

        mock_msg = self.make_mock_response(json.dumps(response_json))
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_msg)

        with patch("app.extractor.client", mock_client):
            result = await extract_recipe(self.FAKE_CAPTION, "https://ig.com/p/test")

        assert result["dish_name"] == "Bolo de Chocolate"
        assert result["distinguishing_feature"] == "whey protein"
        assert result["type"] == "sweet"
        assert result["calories_per_portion"] == 350
        assert result["prep_time_minutes"] == 10
        assert result["cook_time_minutes"] == 30
        assert result["portions"] == 8
        assert "missing_critical_info" in result

    @pytest.mark.asyncio
    async def test_handles_incomplete_extraction(self):
        response_json = {
            "dish_name": "Salada Misteriosa",
            "distinguishing_feature": None,
            "type": "savory",
            "subtype": "salad",
            "macro_tags": [],
            "calories_per_portion": None,
            "ingredients": [],
            "prep_time_minutes": None,
            "cook_time_minutes": None,
            "portions": None,
            "instructions": None,
            "missing_critical_info": True,
        }

        mock_msg = self.make_mock_response(json.dumps(response_json))
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_msg)

        with patch("app.extractor.client", mock_client):
            result = await extract_recipe("Something unhelpful", "https://ig.com/p/test2")

        assert result["missing_critical_info"] is True
        assert result["dish_name"] == "Salada Misteriosa"

    @pytest.mark.asyncio
    async def test_retries_on_invalid_json(self):
        mock_client = AsyncMock()
        # First call returns garbage, second returns valid JSON
        bad_msg = self.make_mock_response("not valid json at all")
        good_msg = self.make_mock_response(json.dumps({
            "dish_name": "Test", "distinguishing_feature": None, "type": "sweet",
            "subtype": None, "macro_tags": [], "calories_per_portion": None,
            "ingredients": [], "prep_time_minutes": None, "cook_time_minutes": None,
            "portions": None, "instructions": None, "missing_critical_info": True,
        }))
        mock_client.messages.create = AsyncMock(side_effect=[bad_msg, good_msg])

        with patch("app.extractor.client", mock_client):
            result = await extract_recipe("caption", "https://ig.com/p/test3")

        assert result["dish_name"] == "Test"
        assert mock_client.messages.create.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_extraction_error_after_two_failures(self):
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            return_value=self.make_mock_response("still not json {{{")
        )

        with patch("app.extractor.client", mock_client):
            with pytest.raises(ExtractionError):
                await extract_recipe("caption", "https://ig.com/p/test4")

        assert mock_client.messages.create.call_count == 2
