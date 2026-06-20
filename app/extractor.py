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
- ingredients: array of strings — normalized ingredient names, singular form, lowercase (e.g., "egg" not "eggs", "chicken breast" not "chicken breasts")
- prep_time_minutes: integer or null — preparation time in minutes
- cook_time_minutes: integer or null — cooking time in minutes (null for no-cook dishes)
- portions: integer or null — number of servings
- instructions: string or null — full preparation steps as markdown. Include all steps mentioned.
- missing_critical_info: boolean — true if the text is missing most fields (e.g., no ingredients, no instructions, no dish name identifiable)

Be conservative: if a field isn't clearly stated, use null. Don't guess calories unless mentioned.
Normalize ingredient names: lowercase, singular, no quantities (e.g., "200g of eggs" → "egg").

Text to extract from:
---
{text}
---"""


class ExtractionError(Exception):
    """Raised when LLM extraction fails after retries."""
    pass


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
                model="claude-sonnet-4-6",
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

        except (json.JSONDecodeError, KeyError, IndexError):
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


def _validate_extraction(data: dict) -> None:
    """Ensure the extracted dict has the required top-level keys."""
    required_keys = {
        "dish_name", "distinguishing_feature", "type", "subtype",
        "macro_tags", "calories_per_portion", "ingredients",
        "prep_time_minutes", "cook_time_minutes", "portions",
        "instructions", "missing_critical_info",
    }
    missing = required_keys - set(data.keys())
    if missing:
        raise KeyError(f"Missing keys in extraction response: {missing}")
    if data["type"] not in ("sweet", "savory"):
        raise ValueError(f"Invalid type: {data['type']}")
