"""Claude-based structured extraction from bill/statement documents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Optional

from anthropic import AsyncAnthropic

from app.config import settings
from app.services.document_input import build_content_block
from app.services.json_utils import strip_json_fences

_MODEL = "claude-sonnet-5"

_SYSTEM_PROMPT = """You extract structured billing data from a bill or bank \
statement document. Respond with ONLY a JSON object, no prose, matching this \
shape exactly:

{
  "provider": "string, the company/entity that issued the bill",
  "category_hint": "one lowercase word: electricity, water, gas, telecom, \
insurance, subscriptions, groceries, health, home, or other",
  "amount": 0.00,
  "currency": "3-letter ISO code, default EUR",
  "due_date": "YYYY-MM-DD or null",
  "paid_date": "YYYY-MM-DD or null",
  "statement_period": "YYYY-MM or null"
}

If a field cannot be determined, use null (or 0.0 for amount as a last resort)."""


class ExtractionError(Exception):
    pass


@dataclass
class ExtractedBill:
    provider: str
    category_hint: str
    amount: float
    currency: str
    due_date: Optional[date]
    paid_date: Optional[date]
    statement_period: Optional[str]


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    return date.fromisoformat(value)


async def extract_bill(file_path: str, client: Optional[AsyncAnthropic] = None) -> ExtractedBill:
    """Extract structured billing data from a bill/statement document (the
    whole PDF, all pages, or a single image)."""
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    content_block = build_content_block(file_path)

    message = await anthropic_client.messages.create(
        model=_MODEL,
        max_tokens=4096,
        thinking={"type": "disabled"},
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": "Extract the billing data as JSON."},
                ],
            }
        ],
    )

    try:
        raw_text = strip_json_fences(message.content[0].text)
        data = json.loads(raw_text)
        return ExtractedBill(
            provider=data["provider"],
            category_hint=data.get("category_hint", "other"),
            amount=float(data.get("amount") or 0.0),
            currency=data.get("currency") or "EUR",
            due_date=_parse_date(data.get("due_date")),
            paid_date=_parse_date(data.get("paid_date")),
            statement_period=data.get("statement_period"),
        )
    except (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ExtractionError(f"Could not parse extraction response: {exc}") from exc


_CLASSIFICATION_MODEL = "claude-haiku-4-5-20251001"

_CLASSIFICATION_SYSTEM_PROMPT = """You classify an uploaded financial \
document as either a single bill/invoice or a bank account statement. \
Respond with ONLY a JSON object:

{"document_type": "bill" or "statement"}

A "bill" has one provider and one amount due (an invoice, receipt, or \
premium notice). A "statement" lists multiple transactions across one or \
more accounts (a monthly bank/account statement)."""


class ClassificationError(Exception):
    pass


async def classify_document(file_path: str, client: Optional[AsyncAnthropic] = None) -> str:
    """Classify an uploaded document as "bill" or "statement"."""
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    content_block = build_content_block(file_path)

    message = await anthropic_client.messages.create(
        model=_CLASSIFICATION_MODEL,
        max_tokens=128,
        thinking={"type": "disabled"},
        system=_CLASSIFICATION_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": "Classify this document as JSON."},
                ],
            }
        ],
    )

    try:
        raw_text = strip_json_fences(message.content[0].text)
        data = json.loads(raw_text)
        document_type = data["document_type"]
        if document_type not in ("bill", "statement"):
            raise ValueError(f"Unexpected document_type: {document_type!r}")
        return document_type
    except (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ClassificationError(f"Could not classify document: {exc}") from exc
