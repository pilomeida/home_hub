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


_STATEMENT_MODEL = "claude-sonnet-5"

_STATEMENT_SYSTEM_PROMPT = """You extract every transaction line item from a \
bank account statement document (all pages). Respond with ONLY a JSON \
object, no prose, matching this shape exactly:

{
  "statement_period": "YYYY-MM",
  "transactions": [
    {
      "date": "YYYY-MM-DD",
      "description": "string, the merchant or counterparty",
      "amount": 0.00, always the positive magnitude of the transaction — \
never negative, regardless of how the source statement renders debits \
(e.g. as negative numbers or a separate debito column); direction is \
conveyed only via the "type" field below, never by the sign of amount,
      "currency": "3-letter ISO code, default EUR",
      "type": "debit, credit, or transfer — use transfer for a move \
between the account holder's own accounts. This includes indirect \
transfers: a Santander statement may show this as a payment to a \
temporary/virtual MB WAY-issued card (used to top up a Revolut account) \
rather than a literal 'transfer to Revolut' line — treat an MB WAY \
temporary card top-up as a transfer, not an ordinary purchase. On a \
Revolut statement, the matching incoming top-up (from Santander, \
directly or via such a card) is also a transfer, not income. A direct \
transfer in the other direction (Revolut back to Santander) is a \
transfer too.",
      "category_hint": "one lowercase word: electricity, water, gas, \
telecom, insurance, subscriptions, groceries, health, home, income, \
transfer, atm_withdrawal, restaurants, shopping, or other_expense"
    }
  ]
}

Include every transaction line item found across all pages of the statement."""


class StatementExtractionError(Exception):
    pass


@dataclass
class ExtractedTransaction:
    transaction_date: date
    description: str
    amount: float
    currency: str
    transaction_type: str
    category_hint: str


@dataclass
class ExtractedStatement:
    statement_period: Optional[str]
    transactions: list[ExtractedTransaction]


async def extract_statement_transactions(
    file_path: str, client: Optional[AsyncAnthropic] = None
) -> ExtractedStatement:
    """Extract every transaction line item from a bank statement document."""
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    content_block = build_content_block(file_path)

    message = await anthropic_client.messages.create(
        model=_STATEMENT_MODEL,
        max_tokens=16384,
        thinking={"type": "disabled"},
        system=_STATEMENT_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": "Extract every transaction as JSON."},
                ],
            }
        ],
    )

    if getattr(message, "stop_reason", None) == "max_tokens":
        raise StatementExtractionError(
            "Statement response was truncated (max_tokens reached) — statement "
            "may have too many transactions for one call"
        )

    try:
        raw_text = strip_json_fences(message.content[0].text)
        data = json.loads(raw_text)
        transactions = [
            ExtractedTransaction(
                transaction_date=date.fromisoformat(item["date"]),
                description=item["description"],
                amount=float(item["amount"]),
                currency=item.get("currency") or "EUR",
                transaction_type=item["type"],
                category_hint=item.get("category_hint", "other_expense"),
            )
            for item in data["transactions"]
        ]
        return ExtractedStatement(statement_period=data.get("statement_period"), transactions=transactions)
    except (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise StatementExtractionError(f"Could not parse statement extraction response: {exc}") from exc


_UTILITY_DETAIL_SYSTEM_PROMPT = """You extract consumption and cost-breakdown \
detail from a utility bill document (electricity, water, or telecom). \
Respond with ONLY a JSON object, no prose, matching this shape exactly:

{
  "period_label": "YYYY-MM — the calendar month holding the majority of \
days in this bill's billing period",
  "billing_period_start": "YYYY-MM-DD or null",
  "billing_period_end": "YYYY-MM-DD or null",
  "invoice_number": "string or null",
  "consumption_value": "number or null — the metered consumption amount \
(e.g. kWh for electricity, m3 for water, GB for telecom)",
  "consumption_unit": "string or null — the unit for consumption_value, \
e.g. kWh, m3, GB",
  "energy_cost": "number or null — the energy/consumption cost component, \
before power/fees/VAT, if the bill itemizes it separately",
  "power_cost": "number or null — a fixed power/capacity charge component, \
if the bill itemizes it separately",
  "fees_taxes_cost": "number or null — other fees and taxes, if itemized \
separately from VAT",
  "vat_cost": "number or null — VAT/tax component, if itemized separately"
}

period_label is required — always determine it even if other fields are \
uncertain. Use null liberally for any other field the bill doesn't clearly \
itemize; do not guess or approximate a value that isn't actually shown."""


class UtilityDetailExtractionError(Exception):
    pass


@dataclass
class ExtractedUtilityDetail:
    period_label: str
    billing_period_start: Optional[date]
    billing_period_end: Optional[date]
    invoice_number: Optional[str]
    consumption_value: Optional[float]
    consumption_unit: Optional[str]
    energy_cost: Optional[float]
    power_cost: Optional[float]
    fees_taxes_cost: Optional[float]
    vat_cost: Optional[float]


async def extract_utility_detail(
    file_path: str, utility_type: str, client: Optional[AsyncAnthropic] = None
) -> ExtractedUtilityDetail:
    """Extract consumption + cost-breakdown detail from a utility bill
    document. Enrichment only — callers must treat failure as non-fatal to
    the underlying bill's own processing."""
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    content_block = build_content_block(file_path)

    message = await anthropic_client.messages.create(
        model=_MODEL,
        max_tokens=1024,
        thinking={"type": "disabled"},
        system=_UTILITY_DETAIL_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    content_block,
                    {
                        "type": "text",
                        "text": f"Extract the {utility_type} consumption/cost detail as JSON.",
                    },
                ],
            }
        ],
    )

    try:
        raw_text = strip_json_fences(message.content[0].text)
        data = json.loads(raw_text)
        return ExtractedUtilityDetail(
            period_label=data["period_label"],
            billing_period_start=_parse_date(data.get("billing_period_start")),
            billing_period_end=_parse_date(data.get("billing_period_end")),
            invoice_number=data.get("invoice_number"),
            consumption_value=(
                float(data["consumption_value"]) if data.get("consumption_value") is not None else None
            ),
            consumption_unit=data.get("consumption_unit"),
            energy_cost=(float(data["energy_cost"]) if data.get("energy_cost") is not None else None),
            power_cost=(float(data["power_cost"]) if data.get("power_cost") is not None else None),
            fees_taxes_cost=(
                float(data["fees_taxes_cost"]) if data.get("fees_taxes_cost") is not None else None
            ),
            vat_cost=(float(data["vat_cost"]) if data.get("vat_cost") is not None else None),
        )
    except (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise UtilityDetailExtractionError(f"Could not parse utility detail response: {exc}") from exc
