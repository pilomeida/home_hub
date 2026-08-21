"""Merchant resolution and transaction classification: normalizes a raw
provider string to a canonical Merchant (rules first, LLM fallback), and
detects recurring-commitment and debt candidates for human review."""

import re

_LOCATION_WORDS = ("mafra", "ericeira")

_STORE_CODE_RE = re.compile(r"\s+\d+-\w+$")
_LOCATION_RE = re.compile(r"(\s+(?:" + "|".join(_LOCATION_WORDS) + r"))+$", re.IGNORECASE)


def normalize_provider(raw: str) -> str:
    """Strip statement-specific noise (trailing store codes, trailing
    known-location words) from a raw provider string, producing a
    normalized lookup key. Real examples this handles: "MODELO HIPER
    2640-MAFR" / "MODELO HIPER MAFRA" / "MODELO HIPER" all normalize to
    "modelo hiper"."""
    key = raw.strip().lower()
    key = _STORE_CODE_RE.sub("", key)
    key = _LOCATION_RE.sub("", key)
    key = re.sub(r"\s+", " ", key).strip()
    return key
