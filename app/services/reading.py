"""The shared reading block (PLAN step 5a): what every reader of a paper uses, so each one does not re-learn it.

- One error type: `ReadingError`. Every reader's own error (a bill, a statement, a Health paper, an Other paper, a
  warranty) is a kind of it, so a caller can catch "the paper could not be read" once.
- Portuguese numbers and dates: `number("1.234,56") == 1234.56`, `day("05/03/2025") == date(2025, 3, 5)`. Models
  usually answer in ISO and plain numbers; when one copies the paper's own print instead, it is still understood.
- A size guard: `check_size(path)` refuses a file too big to send in one go with a plain reason (the Problems page
  explains "too large"), instead of a provider error after the upload; `cut_text` caps a text and says if it cut.
- One JSON reading: `json_object(text, error=...)` returns the object the model wrote or raises the reader's error.
- The paper's own text (step 1): `own_text(path)` is the text a PDF carries itself, or None (a scan, a photo)."""

from __future__ import annotations

import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Optional, Type

from app.services.json_utils import strip_json_fences

MAX_FILE_BYTES = 25 * 1024 * 1024        # larger files are refused before upload (providers stop near 32 MB)
MAX_TEXT_CHARS = 120_000                 # a reader's prompt text; a longer paper is cut and the reader told so


class ReadingError(Exception):
    """A paper could not be read. Every reader's error is one of these."""


class FileTooLargeError(ReadingError):
    pass


# --- numbers -------------------------------------------------------------------------------------

_NUMBER = re.compile(r"^(?:€|EUR|\$|£)?\s*([+\-−–]?)\s*(\d[\d.,  ]*)\s*(?:€|EUR)?\s*(-?)$")


def number(value) -> Optional[float]:
    """A number as a model or a paper writes it: 12.5, "12.5", "1.234,56", "1,234.56", "-12,30 €", "1 234,56",
    "12,30-" (a trailing minus, as some statements print debits). None for anything else (missing, words, a
    boolean, NaN, infinity): an unreadable figure means "not printed".
    A lone comma is the decimal mark (Portuguese); a lone dot is the decimal mark too (the model's own number),
    unless the figure is written as money with dot thousands ("1.234 €", "€ 12.345.678")."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    text = str(value).strip().replace("\u00a0", " ")
    match = _NUMBER.match(text)
    if not match:
        return None
    sign, digits, trailing = match.group(1), re.sub(r"\s", "", match.group(2)), match.group(3)
    money = "€" in text or "EUR" in text
    if "," in digits and "." in digits:                       # both: the last one is the decimal mark
        thousands = "." if digits.rfind(",") > digits.rfind(".") else ","
        digits = digits.replace(thousands, "").replace(",", ".")
    elif digits.count(",") > 1:                               # 1,234,567
        digits = digits.replace(",", "")
    elif "," in digits:                                       # 12,30
        digits = digits.replace(",", ".")
    elif digits.count(".") > 1 or (money and re.fullmatch(r"\d{1,3}(\.\d{3})+", digits)):
        digits = digits.replace(".", "")                      # 1.234.567, or 1.234 € (Portuguese thousands)
    try:
        result = float(digits)
    except ValueError:
        return None
    if not math.isfinite(result):
        return None
    return -result if sign or trailing else result


# --- dates ---------------------------------------------------------------------------------------

_DMY = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})\s*$")
_YMD = re.compile(r"^\s*(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})")


def day(value) -> Optional[date]:
    """A date as a model or a Portuguese paper writes it: "2025-03-05", "2025-03-05T10:00", "05/03/2025",
    "5-3-2025", "05.03.25". Day first, always (Portuguese papers never print month first). None otherwise."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for pattern, order in ((_YMD, "ymd"), (_DMY, "dmy")):
        match = pattern.match(text)
        if not match:
            continue
        a, b, c = (int(g) for g in match.groups())
        y, m, d = (a, b, c) if order == "ymd" else (c if c >= 100 else 2000 + c, b, a)
        try:
            return date(y, m, d)
        except ValueError:
            return None
    return None


# --- size guard and text -------------------------------------------------------------------------

def check_size(file_path: str, limit: int = MAX_FILE_BYTES) -> None:
    """Refuse a file too large to send in one go, before anything is uploaded."""
    size = Path(file_path).stat().st_size
    if size > limit:
        raise FileTooLargeError(
            f"File too large to read in one go: {size / 1_048_576:.1f} MB, the limit is {limit / 1_048_576:.0f} MB")


def cut_text(text: str, limit: int = MAX_TEXT_CHARS) -> tuple[str, bool]:
    """The text, at most `limit` characters, and whether it was cut."""
    return (text, False) if len(text) <= limit else (text[:limit], True)


def own_text(file_path: str) -> Optional[str]:
    """The text a PDF carries itself (step 1's text layer), or None for a scan, a photo or another file."""
    from app.services.paper_search import pdf_text_of
    return pdf_text_of(file_path)


# --- the model's answer --------------------------------------------------------------------------

def json_object(text: Optional[str], *, error: Type[ReadingError] = ReadingError) -> dict:
    """The JSON object a model answered with (code fences tolerated), or the reader's error."""
    try:
        data = json.loads(strip_json_fences(text or ""))
    except (ValueError, TypeError) as exc:
        raise error(f"Could not parse the reading: {exc}") from exc
    if not isinstance(data, dict):
        raise error("Could not parse the reading: not an object")
    return data


__all__ = ["ReadingError", "FileTooLargeError", "MAX_FILE_BYTES", "MAX_TEXT_CHARS", "number", "day", "check_size",
           "cut_text", "own_text", "json_object"]
