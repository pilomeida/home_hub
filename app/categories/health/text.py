"""Name folding for matching people, areas and tests ('Évora' == 'evora')."""

import re
from typing import Optional
import unicodedata


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def match_key(text: str) -> str:
    return re.sub(r"\s+", " ", fold(text)).strip()


PATH_SEP = " › "


def spaced_intervals(text: Optional[str]) -> Optional[str]:
    """'4,20-5,50' / '4,20 – 5,50' -> '4,20 - 5,50' (Pedro: a space either side of the dash in an interval)."""
    if text is None:
        return None
    return re.sub(r"(?<=\d)\s*[-–—]\s*(?=\d)", " - ", text).strip()


def reference_for_display(reference_text: Optional[str], ref_low: Optional[float], ref_high: Optional[float]) -> str:
    """What the paper printed as reference; when only the numbers were kept, the interval or limit they make."""
    if reference_text and reference_text.strip():
        return spaced_intervals(reference_text) or ""
    if ref_low is not None and ref_high is not None:
        return f"{ref_low:g} - {ref_high:g}"
    if ref_high is not None:
        return f"< {ref_high:g}"
    if ref_low is not None:
        return f"> {ref_low:g}"
    return ""


_METHOD = re.compile(r"\(\s*(?:m[ée]t(?:odo)?\.?|method)\s*:?\s*([^)]*)\)", re.IGNORECASE)


def split_method(name: str) -> tuple[str, Optional[str]]:
    """'CREATININA (Mét.Jaffé cinético)' -> ('CREATININA', 'Jaffé cinético'): the method is data, but it is not
    part of the test's name (a lab changing its method must not make the same test look like another one)."""
    methods = [m.strip() for m in _METHOD.findall(name or "") if m.strip()]
    clean = re.sub(r"\s+", " ", _METHOD.sub("", name or "")).strip(" -")
    return clean, "; ".join(methods) or None


def _number(raw: str) -> Optional[float]:
    try:
        return float(raw.strip().replace(" ", "").replace(",", "."))
    except ValueError:
        return None


_NUM = r"(\d+(?:[.,]\d+)?)"
_INTERVAL = re.compile(rf"^\s*{_NUM}\s*[-–—]\s*{_NUM}\s*$")
_UPPER = re.compile(rf"^\s*(?:<|=<|<=|≤)\s*{_NUM}\s*$")
_LOWER = re.compile(rf"^\s*(?:>|>=|=>|≥)\s*{_NUM}\s*$")
_LOW_RISK = re.compile(r"risco\s+baixo\s*:?\s*([<>])\s*=?\s*(\d+(?:[.,]\d+)?)|low\s+risk\s*:?\s*([<>])\s*=?\s*(\d+(?:[.,]\d+)?)", re.I)

_HIGH_RISK = re.compile(r"(?:risco\s+elevado|high\s+risk)\s*:?\s*([<>])\s*=?\s*(\d+(?:[.,]\d+)?)", re.I)


def limits_from_reference(reference: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    """(low, high) of what the paper printed as reference, when it is a plain interval ('4,20 - 5,50') or a one-sided
    limit ('< 20'); for a test graded by risk, its low-risk limit. Anything else (words, mixed notes): (None, None)."""
    if not reference:
        return None, None
    m = _INTERVAL.match(reference)
    if m:
        return _number(m.group(1)), _number(m.group(2))
    m = _UPPER.match(reference)
    if m:
        return None, _number(m.group(1))
    m = _LOWER.match(reference)
    if m:
        return _number(m.group(1)), None
    m = _LOW_RISK.search(reference)
    if m:
        sign, raw = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        return (None, _number(raw)) if sign == "<" else (_number(raw), None)
    m = _HIGH_RISK.search(reference)       # only the high-risk threshold is printed: beyond it, the result is out of range
    if m:
        sign, raw = m.group(1), m.group(2)
        return (None, _number(raw)) if sign == ">" else (_number(raw), None)
    leading = re.match(rf"^\s*(?:<|=<|<=|≤)\s*{_NUM}\b", reference)      # '< 0,50 (> 4,00 sugestivo ...)': a limit plus a note
    if leading:
        return None, _number(leading.group(1))
    return None, None
