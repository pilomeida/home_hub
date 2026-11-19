"""The Other papers reader (PLAN step 1c): title, date, summary and who or what the paper is about.

It reads the paper's own text when it has one (step 1a, free and exact) and the page itself otherwise. What a person
already typed is kept: the reader fills only the empty details. The summary and title stay in the paper's own
language (Pedro, 2026-11-19, step 6). Prompts adapted from icereed/paperless-gpt `default_prompts/` (MIT)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.llm_gateway import VISION_EXTRACTION, get_gateway
from app.services.document_input import build_content_block
from app.services.json_utils import strip_json_fences

_MAX_TEXT = 30_000

_SYSTEM = """You read one household paper that fits none of the household's usual areas (a contract, a letter, a
certificate, a receipt, a notice...). Return ONLY a JSON object:
{"title": "...", "date": "YYYY-MM-DD or null", "about": "...", "summary": "..."}
- title: a short, specific title, at most 8 words, naming the paper's sender and what it is
  (e.g. "Vodafone - service contract", "Handelsbanken - FATCA/CRS self-certification").
- date: the date the paper was issued or signed, as printed (dd/mm/aaaa in Portuguese papers); null if none.
- about: who or what the paper is about: a person's name, a property, a car, an account; "" if not clear.
- summary: two or three plain sentences: what the paper is, what it asks or states, any amount, deadline or
  reference number that matters.
Write title, about and summary in the paper's own language, with names, numbers and dates exactly as printed."""

_SCHEMA = {"type": "object", "properties": {
    "title": {"type": ["string", "null"]}, "date": {"type": ["string", "null"]},
    "about": {"type": ["string", "null"]}, "summary": {"type": ["string", "null"]}}}


class OtherReadingError(Exception):
    pass


@dataclass(frozen=True)
class OtherReading:
    title: str
    paper_date: Optional[date]
    about: str
    summary: str


def _iso(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


async def read_other_paper(file_path: str, text: Optional[str], *, gateway=None) -> OtherReading:
    gw = gateway or get_gateway()
    if text:
        user, attachments = "The paper's text, taken from its PDF:\n\n" + text[:_MAX_TEXT] + "\n\nRead it as JSON.", None
    else:
        user, attachments = "Read this paper as JSON.", [await asyncio.to_thread(build_content_block, file_path)]
    result = await gw.run(VISION_EXTRACTION, _SYSTEM, user, attachments=attachments, response_schema=_SCHEMA)
    try:
        data = json.loads(strip_json_fences(result.text))
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (ValueError, TypeError) as exc:
        raise OtherReadingError(f"Could not parse the reading: {exc}") from exc
    clean = {k: " ".join(str(data.get(k) or "").split()) for k in ("title", "about", "summary")}
    return OtherReading(clean["title"][:120], _iso(data.get("date")), clean["about"][:120], clean["summary"][:1500])
