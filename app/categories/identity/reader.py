"""The Identity & Legal reader (PLAN step 8a): holder, number, issue and expiry dates, issuing country.

The category is always read at High (forced, like Health): only the private route sees these papers. What a person
typed is kept; the reader fills only empty details."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.llm_gateway import VISION_EXTRACTION, get_gateway
from app.services.document_input import build_content_block
from app.services.reading import ReadingError, cut_text, day, json_object

_MAX_TEXT = 20_000

_SYSTEM = """You read one identity or legal paper of a household member: an ID card (Cartao de Cidadao), a
passport, a driving licence, a residence permit or certificate, or a will. Return ONLY a JSON object:
{"holder": "...", "number": "...", "issued_on": "YYYY-MM-DD or null", "expires_on": "YYYY-MM-DD or null",
 "country": "..."}
- holder: the full name of the person the paper belongs to (for a will, the person who made it), as printed.
- number: the document's own number, as printed; "" for a will or if none is printed.
- issued_on: the date of issue; expires_on: the expiry or "valid until" date (Portuguese papers print dd/mm/aaaa
  or dd mm aaaa); null when not printed. A will has no expiry.
- country: the country that issued it, in English (e.g. "Portugal", "United Kingdom"); "" if not clear."""

_SCHEMA = {"type": "object", "properties": {
    "holder": {"type": ["string", "null"]}, "number": {"type": ["string", "null"]},
    "issued_on": {"type": ["string", "null"]}, "expires_on": {"type": ["string", "null"]},
    "country": {"type": ["string", "null"]}}}


class IdentityReadingError(ReadingError):
    pass


@dataclass(frozen=True)
class IdentityReading:
    holder: str
    number: str
    issued_on: Optional[date]
    expires_on: Optional[date]
    country: str


async def read_identity_paper(file_path: str, text: Optional[str], *, gateway=None) -> IdentityReading:
    gw = gateway or get_gateway()
    if text:
        body, _cut = cut_text(text, _MAX_TEXT)
        user, attachments = "The paper's text, taken from its PDF:\n\n" + body + "\n\nRead it as JSON.", None
    else:
        user, attachments = "Read this paper as JSON.", [await asyncio.to_thread(build_content_block, file_path)]
    result = await gw.run(VISION_EXTRACTION, _SYSTEM, user, attachments=attachments, response_schema=_SCHEMA)
    data = json_object(result.text, error=IdentityReadingError)
    clean = {k: " ".join(str(data.get(k) or "").split()) for k in ("holder", "number", "country")}
    return IdentityReading(clean["holder"][:120], clean["number"][:60], day(data.get("issued_on")),
                           day(data.get("expires_on")), clean["country"][:60])
