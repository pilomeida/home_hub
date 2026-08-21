"""Merchant resolution and transaction classification: normalizes a raw
provider string to a canonical Merchant (rules first, LLM fallback), and
detects recurring-commitment and debt candidates for human review."""

import json
import re
from dataclasses import dataclass
from typing import Optional

from anthropic import AsyncAnthropic

from app.config import settings
from app.models.transaction import Category, Nature
from app.services.json_utils import strip_json_fences

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


_MERCHANT_MODEL = "claude-haiku-4-5-20251001"

_MERCHANT_SYSTEM_PROMPT = """You resolve a raw bank statement provider \
string to a canonical merchant identity. Respond with ONLY a JSON object, \
no prose, matching this shape exactly:

{
  "canonical_name": "string, a clean human-readable merchant name, e.g. \
'Modelo Hiper'",
  "category": "one of: electricity, water, gas, telecom, insurance, \
subscriptions, groceries, health, home, income, transfer, atm_withdrawal, \
restaurants, shopping, other_expense, other",
  "nature": "essential or discretionary"
}"""


class MerchantResolutionError(Exception):
    pass


@dataclass
class ResolvedMerchant:
    canonical_name: str
    category: Category
    nature: Nature


async def resolve_merchant_via_llm(
    raw_provider: str, client: Optional[AsyncAnthropic] = None
) -> ResolvedMerchant:
    """Ask Claude to resolve a raw provider string to a canonical merchant
    name, category, and nature. Called only when the rules tier
    (normalize_provider + a Merchant lookup) finds no existing match."""
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    message = await anthropic_client.messages.create(
        model=_MERCHANT_MODEL,
        max_tokens=256,
        thinking={"type": "disabled"},
        system=_MERCHANT_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": f"Resolve this provider string as JSON: {raw_provider!r}"}
        ],
    )

    try:
        raw_text = strip_json_fences(message.content[0].text)
        data = json.loads(raw_text)
        return ResolvedMerchant(
            canonical_name=data["canonical_name"],
            category=Category(data["category"]),
            nature=Nature(data["nature"]),
        )
    except (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise MerchantResolutionError(f"Could not resolve merchant: {exc}") from exc
