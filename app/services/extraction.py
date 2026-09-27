"""Structured extraction from bill/statement documents via the LLM gateway
(llmsel). Every call sends `build_content_block(...)` output as gateway
`attachments`; the document never names a model — the gateway picks one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.llm_gateway import (
    CLASSIFICATION,
    CRITIQUE_REVIEW,
    INTERACTIVE_RESEARCH,
    VISION_CLASSIFICATION,
    VISION_EXTRACTION,
    get_gateway,
)
from app.services.document_input import build_content_block
from app.services.json_utils import strip_json_fences

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

# No stricter than the prompt ("If a field cannot be determined, use null")
# and the parser (defaults: category_hint→other, amount→0.0, currency→EUR).
# `provider` is required because the parser indexes it directly.
_BILL_SCHEMA = {
    "type": "object",
    "required": ["provider"],
    "properties": {
        "provider": {"type": ["string", "null"]},
        "category_hint": {
            "type": ["string", "null"],
            "enum": ["electricity", "water", "gas", "telecom", "insurance",
                     "subscriptions", "groceries", "health", "home", "other", None],
        },
        "amount": {"type": ["number", "null"]},
        "currency": {"type": ["string", "null"], "minLength": 3, "maxLength": 3},
        "due_date": {"type": ["string", "null"], "format": "date"},
        "paid_date": {"type": ["string", "null"], "format": "date"},
        "statement_period": {"type": ["string", "null"]},
    },
}

_EXTRACT_USER_PROMPT = "Extract the billing data as JSON."


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


async def extract_bill(file_path: str, gateway=None) -> ExtractedBill:
    """Extract structured billing data from a bill/statement document (the
    whole PDF, all pages, or a single image)."""
    gw = gateway or get_gateway()
    content_block = build_content_block(file_path)

    result = await gw.run(
        VISION_EXTRACTION,
        _SYSTEM_PROMPT,
        _EXTRACT_USER_PROMPT,
        attachments=[content_block],
        response_schema=_BILL_SCHEMA,
    )

    try:
        data = json.loads(strip_json_fences(result.text))
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


_CLASSIFICATION_SYSTEM_PROMPT = """You classify an uploaded financial \
document as either a single bill/invoice or a bank account statement. \
Respond with ONLY a JSON object:

{"document_type": "bill" or "statement"}

A "bill" has one provider and one amount due (an invoice, receipt, or \
premium notice). A "statement" lists multiple transactions across one or \
more accounts (a monthly bank/account statement)."""

_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "required": ["document_type"],
    "properties": {"document_type": {"type": "string", "enum": ["bill", "statement"]}},
}

_CLASSIFY_USER_PROMPT = "Classify this document as JSON."


class ClassificationError(Exception):
    pass


async def classify_document(file_path: str, gateway=None) -> str:
    """Classify an uploaded document as "bill" or "statement"."""
    gw = gateway or get_gateway()
    content_block = build_content_block(file_path)

    result = await gw.run(
        VISION_CLASSIFICATION,
        _CLASSIFICATION_SYSTEM_PROMPT,
        _CLASSIFY_USER_PROMPT,
        attachments=[content_block],
        response_schema=_CLASSIFICATION_SCHEMA,
    )

    try:
        data = json.loads(strip_json_fences(result.text))
        document_type = data["document_type"]
        if document_type not in ("bill", "statement"):
            raise ValueError(f"Unexpected document_type: {document_type!r}")
        return document_type
    except (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ClassificationError(f"Could not classify document: {exc}") from exc


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

_TRANSACTION_CATEGORIES = [
    "electricity", "water", "gas", "telecom", "insurance", "subscriptions",
    "groceries", "health", "home", "income", "transfer", "atm_withdrawal",
    "restaurants", "shopping", "other_expense",
]

# Parser: data["transactions"] indexed directly (required); statement_period
# via .get (not required). Inside items: date/description/amount/type are
# indexed directly (required); currency/category_hint are .get-defaulted
# (optional, but null still tolerated — no stricter than the parser).
_STATEMENT_SCHEMA = {
    "type": "object",
    "required": ["transactions"],
    "properties": {
        "statement_period": {"type": ["string", "null"]},
        "transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["date", "description", "amount", "type"],
                "properties": {
                    "date": {"type": ["string", "null"], "format": "date"},
                    "description": {"type": ["string", "null"]},
                    "amount": {"type": ["number", "null"]},
                    "currency": {"type": ["string", "null"], "minLength": 3, "maxLength": 3},
                    "type": {
                        "type": ["string", "null"],
                        "enum": ["debit", "credit", "transfer", None],
                    },
                    "category_hint": {
                        "type": ["string", "null"],
                        "enum": [*_TRANSACTION_CATEGORIES, None],
                    },
                },
            },
        },
    },
}

_STATEMENT_USER_PROMPT = "Extract every transaction as JSON."


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


async def extract_statement_transactions(file_path: str, gateway=None) -> ExtractedStatement:
    """Extract every transaction line item from a bank statement document."""
    gw = gateway or get_gateway()
    content_block = build_content_block(file_path)

    result = await gw.run(
        VISION_EXTRACTION,
        _STATEMENT_SYSTEM_PROMPT,
        _STATEMENT_USER_PROMPT,
        attachments=[content_block],
        response_schema=_STATEMENT_SCHEMA,
    )

    if result.stop_reason == "max_tokens":
        raise StatementExtractionError(
            "Statement response was truncated (max_tokens reached) — statement "
            "may have too many transactions for one call"
        )

    try:
        data = json.loads(strip_json_fences(result.text))
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

_UTILITY_SCHEMA = {
    "type": "object",
    "required": ["period_label"],
    "properties": {
        "period_label": {"type": "string"},
        "billing_period_start": {"type": ["string", "null"], "format": "date"},
        "billing_period_end": {"type": ["string", "null"], "format": "date"},
        "invoice_number": {"type": ["string", "null"]},
        "consumption_value": {"type": ["number", "null"]},
        "consumption_unit": {"type": ["string", "null"]},
        "energy_cost": {"type": ["number", "null"]},
        "power_cost": {"type": ["number", "null"]},
        "fees_taxes_cost": {"type": ["number", "null"]},
        "vat_cost": {"type": ["number", "null"]},
    },
}


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
    file_path: str, utility_type: str, gateway=None
) -> ExtractedUtilityDetail:
    """Extract consumption + cost-breakdown detail from a utility bill
    document. Enrichment only — callers must treat failure as non-fatal to
    the underlying bill's own processing."""
    gw = gateway or get_gateway()
    content_block = build_content_block(file_path)

    result = await gw.run(
        VISION_EXTRACTION,
        _UTILITY_DETAIL_SYSTEM_PROMPT,
        f"Extract the {utility_type} consumption/cost detail as JSON.",
        attachments=[content_block],
        response_schema=_UTILITY_SCHEMA,
    )

    try:
        data = json.loads(strip_json_fences(result.text))
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
