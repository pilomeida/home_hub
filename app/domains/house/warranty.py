"""Warranty tracking -- the one place House uses an LLM (settled in the
2026-09-24 design, Q10): best-effort extraction of a warranty's expiry
date, and the renewal reminder To-Do due REMINDER_LEAD_DAYS before it."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from anthropic import AsyncAnthropic
from sqlmodel import Session

from app.config import settings
from app.domains.fields import field_date, load_fields
from app.domains.house.categories import HouseCategory
from app.models.document import Document
from app.models.domain import Domain
from app.models.todo import Todo
from app.services.document_input import UnsupportedFileTypeError, build_content_block
from app.services.json_utils import strip_json_fences
from app.services.todo_engine import upsert_document_todo

REMINDER_LEAD_DAYS = 21
LEGAL_GUARANTEE_YEARS = 3

_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = """You read a purchase invoice, receipt or warranty \
certificate for a household item. Respond with ONLY a JSON object:

{"expiry_date": "YYYY-MM-DD" or null, "purchase_date": "YYYY-MM-DD" or null}

expiry_date: an explicitly stated warranty/guarantee ("garantia") expiry or \
renewal date; or, if the document states a warranty duration (e.g. "2 anos \
de garantia") and a purchase date, that date plus the duration. Do NOT apply \
any legal default yourself -- if no expiry or duration is stated, use null.
purchase_date: the purchase / invoice / receipt date, if stated.
The document may be in Portuguese or English."""


class WarrantyExtractionError(Exception):
    pass


@dataclass(frozen=True)
class WarrantyDates:
    expiry: Optional[date]
    purchase_date: Optional[date]


@dataclass(frozen=True)
class WarrantyExpiry:
    date: date
    assumed: bool
    purchase_date: Optional[date]


def reminder_due_date(expiry: date) -> date:
    return expiry - timedelta(days=REMINDER_LEAD_DAYS)


async def extract_warranty_dates(file_path: str, client: Optional[AsyncAnthropic] = None) -> WarrantyDates:
    try:
        content_block = build_content_block(file_path)
    except UnsupportedFileTypeError:
        return WarrantyDates(None, None)
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    message = await anthropic_client.messages.create(
        model=_MODEL, max_tokens=128, thinking={"type": "disabled"}, system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [content_block, {"type": "text", "text": "Find the warranty dates as JSON."}]}],
    )
    try:
        data = json.loads(strip_json_fences(message.content[0].text))
        expiry, purchase = data["expiry_date"], data["purchase_date"]
        return WarrantyDates(
            date.fromisoformat(expiry) if expiry else None,
            date.fromisoformat(purchase) if purchase else None,
        )
    except (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise WarrantyExtractionError(f"Could not read the warranty dates: {exc}") from exc


def _add_years(start: date, years: int) -> date:
    try:
        return start.replace(year=start.year + years)
    except ValueError:  # 29 February -> 28 February
        return start.replace(year=start.year + years, day=28)


def effective_warranty_expiry(fields) -> Optional[WarrantyExpiry]:
    purchase = field_date(fields, "purchase_date")
    stated = field_date(fields, "warranty_expiry")
    if stated is not None:
        return WarrantyExpiry(stated, False, purchase)
    if purchase is not None:
        return WarrantyExpiry(_add_years(purchase, LEGAL_GUARANTEE_YEARS), True, purchase)
    return None


def assumed_note(expiry: WarrantyExpiry) -> str:
    return (f"Assumed: Portugal's {LEGAL_GUARANTEE_YEARS}-year legal guarantee from the purchase date "
            f"({expiry.purchase_date.strftime('%d %b %Y')})")


def house_derived_fields(category, fields) -> dict[str, str]:
    if category != HouseCategory.WARRANTY_INVOICE.value:
        return {}
    expiry = effective_warranty_expiry(fields)
    if expiry is None:
        return {}
    derived = {"effective_warranty_expiry": expiry.date.isoformat()}
    if expiry.assumed:
        derived["warranty_expiry_note"] = assumed_note(expiry)
    return derived


def sync_warranty_todo(session: Session, document: Document, today: Optional[date] = None) -> Optional[Todo]:
    """Create/update the renewal reminder for a warranty document. No
    reminder when the expiry is unknown or already past (nothing to renew in
    time)."""
    if document.category != HouseCategory.WARRANTY_INVOICE.value:
        return None
    fields = load_fields(document)
    effective = effective_warranty_expiry(fields)
    item_name = fields.get("item_name")
    if effective is None or not item_name:
        return None
    if effective.date < (today or date.today()):
        return None
    return upsert_document_todo(
        session, document, title=f"Renew {item_name} warranty",
        due_date=reminder_due_date(effective.date), domain=Domain.HOUSE,
    )
