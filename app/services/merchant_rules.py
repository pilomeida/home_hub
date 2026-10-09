"""Keyword rules: a merchant whose name matches is filed under a fixed node, no LLM.

Pedro's rulings (2026-10-07): these chains are always supermarket spending."""

import re
from typing import Optional

SUPERMARKET_SLUG = "food.groceries.supermarket"
_SUPERMARKET = re.compile(r"\b(?:intermarch[eé]|continente|modelo|aldi|lidl|pingo\s+doce)\b", re.IGNORECASE)

KEYWORD_RULES: list[tuple[re.Pattern, str]] = [(_SUPERMARKET, SUPERMARKET_SLUG)]


# The counterparty is the chain, not the store: 'MODELO HIPER 2640-MAFR' is merchant 'Modelo Hiper'.
CHAIN_NAMES = {"intermarche": "Intermarché", "intermarché": "Intermarché", "continente": "Continente", "modelo": "Modelo Hiper",
               "aldi": "Aldi", "lidl": "Lidl", "pingo doce": "Pingo Doce"}


def keyword_match(*texts: Optional[str]) -> Optional[tuple[str, str]]:
    """(node slug, chain name) of the first rule matching any of the texts, or None."""
    for pattern, slug in KEYWORD_RULES:
        for text in texts:
            found = pattern.search(text) if text else None
            if found:
                key = re.sub(r"\s+", " ", found.group(0).lower())
                return slug, CHAIN_NAMES.get(key, found.group(0).title())
    return None


def keyword_node_slug(*texts: Optional[str]) -> Optional[str]:
    """Slug of the first rule matching any of the texts (merchant name, bank provider string)."""
    match = keyword_match(*texts)
    return match[0] if match else None


# Counterparties that sell many kinds of things: a bank's mortgage, savings fund, card payment and fees are
# all "Santander". Their entries are classified by the description, never by one default category.
MULTI_PURPOSE = {"santander", "revolut", "millennium bcp", "bcp", "novo banco", "caixa geral de depositos", "paypal", "aegon santander"}


def is_multi_purpose(name: Optional[str]) -> bool:
    from app.services.merchant_merge import name_key
    return name_key(name) in MULTI_PURPOSE
