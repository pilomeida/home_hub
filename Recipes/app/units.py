"""Convert imperial ingredient quantities to metric at recipe ingestion.

Leaves cups, tablespoons, teaspoons, and already-metric units untouched.
"""
import re
from fractions import Fraction

_UNICODE_FRACTIONS = {
    "½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4",
    "¾": "3/4", "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8",
}

# Imperial unit → (base metric unit, multiply-by factor)
# All volumes convert to ml; all masses convert to g.
# The formatter promotes to L / kg for large values.
_CONV: dict[str, tuple[str, float]] = {
    "gallon":        ("ml", 3785.41),
    "gallons":       ("ml", 3785.41),
    "gal":           ("ml", 3785.41),
    "quart":         ("ml", 946.353),
    "quarts":        ("ml", 946.353),
    "qt":            ("ml", 946.353),
    "fluid ounce":   ("ml", 29.5735),
    "fluid ounces":  ("ml", 29.5735),
    "fl oz":         ("ml", 29.5735),
    "fl. oz":        ("ml", 29.5735),
    "pint":          ("ml", 473.176),
    "pints":         ("ml", 473.176),
    "pt":            ("ml", 473.176),
    "pound":         ("g",  453.592),
    "pounds":        ("g",  453.592),
    "lbs":           ("g",  453.592),
    "lb":            ("g",  453.592),
    "ounce":         ("g",  28.3495),
    "ounces":        ("g",  28.3495),
    "oz":            ("g",  28.3495),
}

# Sort keys longest-first so multi-word units (e.g. "fluid ounces") are tried
# before shorter ones (e.g. "oz") — prevents partial matches.
_UNIT_PAT = "|".join(re.escape(u) for u in sorted(_CONV, key=len, reverse=True))
# Quantity: "1 1/2" | "3/4" | "2.5" | "2"
_QTY_PAT = r"\d+(?:\.\d+)?(?:\s+\d+/\d+)?|\d+/\d+"
_RE = re.compile(rf"({_QTY_PAT})\s*({_UNIT_PAT})\b", re.IGNORECASE)


def _parse_qty(s: str) -> float:
    s = s.strip()
    parts = s.split()
    if len(parts) == 2:           # mixed number: "1 1/2"
        return float(parts[0]) + float(Fraction(parts[1]))
    return float(Fraction(s))     # "3/4", "2", "2.5"


def _fmt(value: float, unit: str) -> str:
    if unit == "g":
        if value >= 1000:
            return f"{round(value / 1000, 1):g}kg"
        return f"{round(value)}g"
    # ml
    if value >= 1000:
        L = round(value / 1000, 1)
        return f"{L:g}L"
    return f"{round(value)}ml"


def _replace(m: re.Match) -> str:
    qty_str, unit_str = m.group(1), m.group(2)
    key = re.sub(r"\s+", " ", unit_str.lower().rstrip("."))
    conv = _CONV.get(key)
    if conv is None:
        return m.group(0)
    metric_unit, factor = conv
    return _fmt(_parse_qty(qty_str) * factor, metric_unit)


def convert_ingredient(s: str) -> str:
    """Convert all imperial units in one ingredient string to metric."""
    for uni, asc in _UNICODE_FRACTIONS.items():
        s = s.replace(uni, asc)
    return _RE.sub(_replace, s)


def convert_ingredients(ingredients: list[str]) -> list[str]:
    """Convert a list of ingredient strings from imperial to metric."""
    return [convert_ingredient(i) for i in ingredients]
