"""LLM-based recipe extraction via Claude API."""

import json
import re
from typing import Any

from anthropic import AsyncAnthropic

from app.config import settings

client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

EXTRACTION_PROMPT = """You are a recipe data extractor. Given the text from a recipe website or Instagram post, extract the following structured information. Return ONLY valid JSON — no commentary, no markdown fences.

Return a JSON object with these keys:
- dish_name: string — the canonical dish name (e.g., "Bolo de Chocolate", "Frango Assado")
- distinguishing_feature: string or null — what makes this version unique (e.g., "whey protein", "low-cal", "air fryer", "vegan")
- type: string — "sweet" or "savory"
- subtype: string or null — one of: "main", "dessert", "snack", "soup", "salad", "breakfast", "side", "drink"
- macro_tags: array of strings — any that apply from: "protein-rich", "low-carb", "keto", "vegan", "gluten-free", "fiber-rich", "high-fat", "dairy-free". Include others that fit.
- calories_per_portion: integer or null — best estimate of calories per serving
- ingredients: array of strings — full ingredient lines with quantities and units exactly as written (e.g., "1 tablespoon rolled oats (40g)", "90g low-fat Greek yogurt"). Preserve amounts.
- prep_time_minutes: integer or null — preparation time in minutes
- cook_time_minutes: integer or null — cooking time in minutes (null for no-cook dishes)
- portions: integer or null — number of servings
- instructions: string or null — full preparation steps as markdown. Include all steps mentioned.
- missing_critical_info: boolean — true if the text is missing most fields (e.g., no ingredients, no instructions, no dish name identifiable)
- cooking_types: array of strings — all that apply from: "oven", "cooktop", "microwave", "blender", "no-cook", "air-fryer", "other". Infer from the instructions text. Can be multiple values. Use [] if no cooking is needed (raw/assembled only).
- protein_g: integer or null — grams of protein per portion. Parse from text like "Protein - 22.8g" or "P 37.3g". Round to nearest integer.
- fat_g: integer or null — grams of fat per portion. Parse from "Fat - 80.9g" or "F 9g".
- carbs_g: integer or null — grams of carbs per portion. Parse from "Carbs - 9.5g" or "C 16.8g".
- fiber_g: integer or null — grams of fiber per portion. Null if not stated.

Be conservative: if a field isn't clearly stated, use null. Don't guess calories unless mentioned.

Text to extract from:
---
{text}
---"""

_CHUNK_PROMPT = """You are extracting recipes from a cookbook page excerpt.

Extract ONLY complete recipes — ones with at minimum a name, ingredient list, and instructions visible in the excerpt. If a recipe is clearly cut off at the start or end of the excerpt (ingredients or instructions missing), skip it.

MACRO ATTRIBUTION RULE: In this cookbook, macro values (KCALS / P / F / C) appear on the SAME page as the recipe title, overlaid on the recipe photo. Always associate a set of macro values with the recipe title on that SAME page. Never assign macros from one page to a recipe whose title is on a different page.

For each complete recipe return an object with these fields:
- dish_name: string
- distinguishing_feature: string or null — what makes this version unique (≤8 words)
- type: "sweet" or "savory"
- subtype: string or null — one of: "main", "dessert", "snack", "soup", "salad", "breakfast", "side", "drink"
- macro_tags: array of strings — any from: "protein-rich", "low-carb", "keto", "vegan", "gluten-free", "fiber-rich", "high-fat", "dairy-free"
- calories_per_portion: integer or null — parse from "KCALS 431" or "CALORIES 431", round to int
- ingredients: array of strings — full lines with quantities and units as written (e.g. "1 tablespoon rolled oats (40g)", "90g low-fat Greek yogurt")
- prep_time_minutes: integer or null
- cook_time_minutes: integer or null
- portions: integer or null
- instructions: string or null — full steps as markdown
- missing_critical_info: boolean — true only if name AND ingredients AND instructions are all missing
- cooking_types: array from: "oven", "cooktop", "microwave", "blender", "no-cook", "air-fryer", "other"
- protein_g: integer or null — parse from "P 46.5g", round to int
- fat_g: integer or null — parse from "F 11.1g", round to int
- carbs_g: integer or null — parse from "C 39.1g", round to int
- fiber_g: integer or null

Return ONLY a valid JSON array. Return [] if no complete recipes are found.

Excerpt:
---
{chunk_text}
---"""


IMAGE_EXTRACTION_PROMPT = """You are a recipe data extractor. These images are from a recipe post — some are screenshots showing the recipe title, ingredients, and instructions; one shows the finished dish. Extract all recipe information from the text in the screenshots.

Return ONLY valid JSON — no commentary, no markdown fences.

Return a JSON object with these keys:
- dish_name: string — the canonical dish name (e.g., "Bolo de Chocolate", "Frango Assado")
- distinguishing_feature: string or null — what makes this version unique (e.g., "whey protein", "low-cal", "air fryer", "vegan")
- type: string — "sweet" or "savory"
- subtype: string or null — one of: "main", "dessert", "snack", "soup", "salad", "breakfast", "side", "drink"
- macro_tags: array of strings — any that apply from: "protein-rich", "low-carb", "keto", "vegan", "gluten-free", "fiber-rich", "high-fat", "dairy-free". Include others that fit.
- calories_per_portion: integer or null — best estimate of calories per serving
- ingredients: array of strings — full ingredient lines with quantities and units exactly as written (e.g., "1 tablespoon rolled oats (40g)", "90g low-fat Greek yogurt"). Preserve amounts.
- prep_time_minutes: integer or null — preparation time in minutes
- cook_time_minutes: integer or null — cooking time in minutes (null for no-cook dishes)
- portions: integer or null — number of servings
- instructions: string or null — full preparation steps as markdown. Include all steps mentioned.
- missing_critical_info: boolean — true if the images are missing most fields (e.g., no ingredients, no instructions, no dish name identifiable)
- cooking_types: array of strings — all that apply from: "oven", "cooktop", "microwave", "blender", "no-cook", "air-fryer", "other". Infer from the instructions text. Can be multiple values. Use [] if no cooking is needed.
- protein_g: integer or null — grams of protein per portion. Parse from text like "Protein - 22.8g" or "P 37.3g". Round to nearest integer.
- fat_g: integer or null — grams of fat per portion. Parse from "Fat - 80.9g" or "F 9g".
- carbs_g: integer or null — grams of carbs per portion. Parse from "Carbs - 9.5g" or "C 16.8g".
- fiber_g: integer or null — grams of fiber per portion. Null if not stated.

Be conservative: if a field isn't clearly stated, use null. Don't guess calories unless mentioned."""


class ExtractionError(Exception):
    """Raised when LLM extraction fails after retries."""
    pass


async def extract_recipe_from_images(image_bytes_list: list[bytes]) -> dict[str, Any]:
    """Extract structured recipe data from a list of images using Claude Vision.

    The images should be recipe screenshots (ingredients/instructions). Pass the
    food photo separately — it is not needed here.

    Args:
        image_bytes_list: Raw bytes for each screenshot image (JPEG or PNG).

    Returns:
        Dict with keys matching the recipe schema.

    Raises:
        ExtractionError: If the list is empty or extraction fails after retries.
    """
    import base64

    if not image_bytes_list:
        raise ExtractionError("No images provided for extraction")

    content: list[dict] = []
    for img_bytes in image_bytes_list:
        b64 = base64.standard_b64encode(img_bytes).decode()
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        })
    content.append({"type": "text", "text": IMAGE_EXTRACTION_PROMPT})

    retry_prefix = ""
    for attempt in range(2):
        try:
            message = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                temperature=0,
                messages=[{"role": "user", "content": content}],
            )
            response_text = message.content[0].text.strip()
            if response_text.startswith("```"):
                response_text = re.sub(r"^```(?:json)?\s*", "", response_text)
                response_text = re.sub(r"\s*```$", "", response_text)
            data = json.loads(response_text)
            _validate_extraction(data)
            return data
        except (json.JSONDecodeError, KeyError, IndexError, ValueError):
            if attempt == 1:
                raise ExtractionError(
                    "Failed to extract valid JSON from vision response after 2 attempts"
                )
            # On retry, replace the text block with a stricter instruction
            content[-1] = {
                "type": "text",
                "text": (
                    "The previous response was not valid JSON. "
                    "You MUST return ONLY valid JSON, no other text.\n\n"
                    + IMAGE_EXTRACTION_PROMPT
                ),
            }

    raise ExtractionError("Unreachable")  # pragma: no cover


async def extract_recipe(text: str, source_url: str) -> dict[str, Any]:
    """Extract structured recipe data from raw text using Claude.

    Args:
        text: Raw scraped text from the recipe source.
        source_url: The original URL (for logging only).

    Returns:
        Dict with keys matching the recipe schema.

    Raises:
        ExtractionError: If extraction fails after retries.
    """
    if not text.strip():
        raise ExtractionError("Empty text provided for extraction")

    prompt = EXTRACTION_PROMPT.format(text=text[:8000])  # truncate for safety

    for attempt in range(2):
        try:
            message = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2048,
                temperature=0.1,
                system="You are a precise recipe data extractor. Return only valid JSON.",
                messages=[{"role": "user", "content": prompt}],
            )

            response_text = message.content[0].text.strip()

            # Strip markdown code fences if present
            if response_text.startswith("```"):
                response_text = re.sub(r"^```(?:json)?\s*", "", response_text)
                response_text = re.sub(r"\s*```$", "", response_text)

            data = json.loads(response_text)
            _validate_extraction(data)
            return data

        except (json.JSONDecodeError, KeyError, IndexError, ValueError):
            if attempt == 1:
                raise ExtractionError(
                    f"Failed to extract valid JSON from LLM response after 2 attempts "
                    f"for {source_url}"
                )
            # Retry with a stricter prompt
            prompt = (
                "The previous response was not valid JSON. "
                "You MUST return ONLY valid JSON, no other text.\n\n"
                + prompt
            )

    raise ExtractionError("Unreachable")  # pragma: no cover


async def extract_recipes_from_chunk(chunk_text: str) -> list[dict]:
    """Extract all complete recipes from a page chunk. Returns list (possibly empty)."""
    if not chunk_text.strip():
        return []

    prompt = _CHUNK_PROMPT.format(chunk_text=chunk_text[:10000])

    for attempt in range(3):
        try:
            message = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                temperature=0,
                system="You are a precise recipe data extractor. Return only valid JSON arrays.",
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = message.content[0].text.strip()
            if response_text.startswith("```"):
                response_text = re.sub(r"^```(?:json)?\s*", "", response_text)
                response_text = re.sub(r"\s*```$", "", response_text)
            parsed = json.loads(response_text)
            if not isinstance(parsed, list):
                continue
            valid = []
            for recipe in parsed:
                try:
                    _validate_extraction(recipe)
                    valid.append(recipe)
                except (KeyError, ValueError):
                    pass
            return valid
        except json.JSONDecodeError:
            continue
    return []


def _validate_extraction(data: dict) -> None:
    """Ensure the extracted dict has the required top-level keys."""
    required_keys = {
        "dish_name", "distinguishing_feature", "type", "subtype",
        "macro_tags", "calories_per_portion", "ingredients",
        "prep_time_minutes", "cook_time_minutes", "portions",
        "instructions", "missing_critical_info",
        "cooking_types", "protein_g", "fat_g", "carbs_g", "fiber_g",
    }
    missing = required_keys - set(data.keys())
    if missing:
        raise KeyError(f"Missing keys in extraction response: {missing}")
    if data["type"] not in ("sweet", "savory"):
        raise ValueError(f"Invalid type: {data['type']}")
