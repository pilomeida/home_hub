"""Keyword rules: a merchant whose name matches is filed under a fixed node, no LLM.

Pedro's rulings (2026-10-07): these chains are always supermarket spending."""

import re
from typing import Optional

SUPERMARKET_SLUG = "food.groceries.supermarket"
_SUPERMARKET = re.compile(r"\b(?:intermarch[eé]|continente|modelo|aldi|lidl|pingo\s+doce)\b", re.IGNORECASE)

KEYWORD_RULES: list[tuple[re.Pattern, str]] = [(_SUPERMARKET, SUPERMARKET_SLUG)]


def keyword_node_slug(*texts: Optional[str]) -> Optional[str]:
    """Slug of the first rule matching any of the texts (merchant name, bank provider string)."""
    for pattern, slug in KEYWORD_RULES:
        if any(text and pattern.search(text) for text in texts):
            return slug
    return None
