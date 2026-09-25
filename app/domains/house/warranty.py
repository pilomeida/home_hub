"""Warranty tracking -- the one place House uses an LLM (settled in the
2026-09-24 design, Q10): best-effort extraction of a warranty's expiry
date, and the renewal reminder To-Do due REMINDER_LEAD_DAYS before it."""

from __future__ import annotations

import json
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

_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = """You read a purchase invoice, receipt or warranty \
certificate and find when its warranty (guarantee / "garantia") expires. \
Respond with ONLY a JSON object:

{"expiry_date": "YYYY-MM-DD" or null}

Use an explicitly stated expiry or renewal date if there is one. Otherwise, \
if the document states both a purchase/invoice date and a warranty duration \
(e.g. "2 anos de garantia"), compute expiry = purchase date + duration. The \
document may be in Portuguese or English. Never assume a legal default \
duration: if the document does not state enough to know, return null."""


class WarrantyExtractionError(Exception):
    pass


def reminder_due_date(expiry: date) -> date:
    return expiry - timedelta(days=REMINDER_LEAD_DAYS)


async def extract_warranty_expiry(file_path: str, client: Optional[AsyncAnthropic] = None) -> Optional[date]:
    try:
        content_block = build_content_block(file_path)
    except UnsupportedFileTypeError:
        return None
    anthropic_client = client or AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    message = await anthropic_client.messages.create(
        model=_MODEL,
        max_tokens=128,
        thinking={"type": "disabled"},
        system=_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [content_block, {"type": "text", "text": "Find the warranty expiry date as JSON."}],
        }],
    )
    try:
        data = json.loads(strip_json_fences(message.content[0].text))
        value = data["expiry_date"]
        return date.fromisoformat(value) if value else None
    except (IndexError, AttributeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise WarrantyExtractionError(f"Could not read the warranty expiry date: {exc}") from exc


def sync_warranty_todo(session: Session, document: Document, today: Optional[date] = None) -> Optional[Todo]:
    """Create/update the renewal reminder for a warranty document. No
    reminder when the expiry is unknown or already past (nothing to renew in
    time)."""
    if document.category != HouseCategory.WARRANTY_INVOICE.value:
        return None
    fields = load_fields(document)
    expiry = field_date(fields, "warranty_expiry")
    item_name = fields.get("item_name")
    if expiry is None or not item_name:
        return None
    if expiry < (today or date.today()):
        return None
    return upsert_document_todo(
        session, document, title=f"Renew {item_name} warranty",
        due_date=reminder_due_date(expiry), domain=Domain.HOUSE,
    )
