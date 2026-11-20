"""Reading a typed paper's details (PLAN step 8): the paper's own text when it has one, the page otherwise; the model
returns the named fields as JSON; only the empty ones are filled, so what a person typed is kept. Dates come back
as ISO or are dropped (never guessed)."""

from __future__ import annotations

import asyncio
from typing import Iterable, Optional

from sqlmodel import Session

from app.llm_gateway import VISION_EXTRACTION, get_gateway
from app.models.document import Document
from app.services.document_input import build_content_block, is_model_readable
from app.services.reading import ReadingError, cut_text, day, json_object

_MAX_TEXT = 20_000


class PaperReadingError(ReadingError):
    pass


async def read_fields(file_path: str, text: Optional[str], system: str, keys: Iterable[str],
                      dates: Iterable[str] = (), *, gateway=None) -> dict[str, str]:
    keys, dates = list(keys), set(dates)
    gw = gateway or get_gateway()
    if text:
        body, _cut = cut_text(text, _MAX_TEXT)
        user, attachments = "The paper's text, taken from its PDF:\n\n" + body + "\n\nRead it as JSON.", None
    else:
        user, attachments = "Read this paper as JSON.", [await asyncio.to_thread(build_content_block, file_path)]
    schema = {"type": "object", "properties": {k: {"type": ["string", "null"]} for k in keys}}
    result = await gw.run(VISION_EXTRACTION, system, user, attachments=attachments, response_schema=schema)
    data = json_object(result.text, error=PaperReadingError)
    out = {}
    for k in keys:
        if k in dates:
            when = day(data.get(k))
            out[k] = when.isoformat() if when else ""
        else:
            out[k] = " ".join(str(data.get(k) or "").split())[:200]
    return out


async def fill_empty(session: Session, document: Document, fields: dict, system: str, keys: Iterable[str],
                     dates: Iterable[str] = ()) -> dict:
    """The paper's fields with the empty ones read from it (nothing is read when none is empty)."""
    from app.services.paper_search import store_pdf_text
    keys = list(keys)
    if not any(not fields.get(k) for k in keys) or not is_model_readable(document.file_path):
        return fields
    row = store_pdf_text(session, document)
    found = await read_fields(document.file_path, row.text if row is not None else None, system, keys, dates)
    return {**{k: v for k, v in found.items() if v}, **{k: v for k, v in fields.items() if v}}
