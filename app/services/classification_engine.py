"""Merchant resolution and transaction classification: normalizes a raw
provider string to a canonical Merchant (rules first, LLM fallback), and
detects recurring-commitment and debt candidates for human review."""

import json
import re
from dataclasses import dataclass
from typing import Optional

from anthropic import AsyncAnthropic
from sqlmodel import Session, select

from app.config import settings
from app.models.document import Document
from app.models.merchant import Merchant
from app.models.transaction import Category, Nature, Transaction
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


@dataclass
class ClassificationResult:
    merchant: Merchant
    created_new_merchant: bool


def _find_merchant_by_key(session: Session, normalized_key: str) -> Optional[Merchant]:
    statement = select(Merchant).where(Merchant.normalized_key == normalized_key)
    return session.exec(statement).first()


async def classify_transaction(
    session: Session, transaction: Transaction, client: Optional[AsyncAnthropic] = None
) -> ClassificationResult:
    """Resolve transaction.provider to a Merchant (rules tier, then LLM
    fallback on a miss), and set transaction.merchant_id/account_id/nature
    from the result. Does not commit — the caller controls the
    transaction boundary. This is the single entry point both the live
    ingestion pipeline and the historical backfill script use."""
    normalized_key = normalize_provider(transaction.provider)
    merchant = _find_merchant_by_key(session, normalized_key)
    created_new_merchant = False

    if merchant is None:
        resolved = await resolve_merchant_via_llm(transaction.provider, client=client)
        merchant = Merchant(
            canonical_name=resolved.canonical_name,
            default_category=resolved.category,
            default_nature=resolved.nature,
            normalized_key=normalized_key,
        )
        session.add(merchant)
        session.flush()
        created_new_merchant = True

    transaction.merchant_id = merchant.id
    transaction.nature = merchant.default_nature

    if transaction.account_id is None:
        document = session.get(Document, transaction.document_id)
        if document is not None and document.account_id is not None:
            transaction.account_id = document.account_id

    session.add(transaction)
    return ClassificationResult(merchant=merchant, created_new_merchant=created_new_merchant)
