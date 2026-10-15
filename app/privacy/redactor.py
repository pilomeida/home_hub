"""Deterministic, CPU-only redaction (decision D3). No LLM, no network.

Structured identifiers are found by shape + checksum (IBAN mod-97, NIB
check digits, Luhn, NIF check digit) so a random number is not mistaken for
one. Names, addresses and policy/contract numbers are found by exact lookup
against values the Hub already knows. What it cannot catch: a value the Hub
has never seen (a stranger's name, a free-text address). `scan` re-checks
the output so an incomplete redaction is detected and the call blocked.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Optional

from app.privacy.tiers import TierBlocked
from app.privacy.vault import Vault


# --- checksums -------------------------------------------------------------

def _mod97(s: str) -> int:
    return int("".join(str(int(c, 36)) for c in s)) % 97


def valid_iban(raw: str) -> bool:
    s = re.sub(r"\s+", "", raw).upper()
    return 15 <= len(s) <= 34 and s[:2].isalpha() and s[2:4].isdigit() and _mod97(s[4:] + s[:4]) == 1


def valid_nib(raw: str) -> bool:
    d = re.sub(r"\s+", "", raw)
    return len(d) == 21 and d.isdigit() and (98 - int(d[:19]) * 100 % 97) == int(d[19:])


def valid_luhn(raw: str) -> bool:
    d = [int(c) for c in re.sub(r"[ -]", "", raw)][::-1]
    return 13 <= len(d) <= 19 and (sum(d[0::2]) + sum(sum(divmod(x * 2, 10)) for x in d[1::2])) % 10 == 0


def valid_nif(raw: str) -> bool:
    d = re.sub(r"\s+", "", raw)
    if len(d) != 9 or not d.isdigit() or d[0] == "0":
        return False
    check = 11 - sum(int(d[i]) * (9 - i) for i in range(8)) % 11
    return (0 if check >= 10 else check) == int(d[8])


# --- detectors: (kind, regex, validator, group) in priority order ----------

_DETECTORS: list[tuple[str, re.Pattern, Optional[callable], int]] = [
    ("IBAN", re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,3})?(?![A-Za-z0-9])"), valid_iban, 0),
    ("NIB", re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{11}\s?\d{2}(?!\d)"), valid_nib, 0),
    ("CARD", re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"), valid_luhn, 0),
    ("EMAIL", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), None, 0),
    ("NIF", re.compile(r"(?i)\b(?:NIF|NIPC|contribuinte)\b[^\d\n]{0,14}(\d{3}\s?\d{3}\s?\d{3})(?!\d)"), None, 1),
    ("PHONE", re.compile(r"(?<![\d+])(?:(?:\+|00)351[ ]?)?[29]\d{2}[ ]?\d{3}[ ]?\d{3}(?!\d)"), None, 0),
    ("NIF", re.compile(r"(?<![\d.,])\d{9}(?![\d])"), valid_nif, 0),
]

# Lookup kinds a caller may supply, in priority order.
LOOKUP_KINDS = ("POLICY", "ACCOUNT", "ADDRESS", "NAME")


def _fold(text: str) -> str:
    """Lower-case and strip accents, one output char per input char so match
    spans on the folded text are valid spans on the original."""
    out = []
    for ch in text:
        base = unicodedata.normalize("NFD", ch)[0]
        low = base.lower()
        out.append(low if len(low) == 1 else base)
    return "".join(out)


@dataclass
class Span:
    start: int
    end: int
    kind: str
    value: str


@dataclass
class RedactionReport:
    """Safe to log: counts and sizes only, never content."""
    chars_in: int
    chars_out: int
    replaced: int
    by_kind: dict[str, int] = field(default_factory=dict)


@dataclass
class Redacted:
    text: str
    report: RedactionReport


class Redactor:
    def __init__(self, known: Optional[dict[str, Iterable[str]]] = None):
        self._lookup: list[tuple[str, re.Pattern]] = []
        for kind in LOOKUP_KINDS:
            values = {v.strip() for v in (known or {}).get(kind, ()) if v and v.strip()}
            if kind == "NAME":  # also each name part, so "Rute" alone is caught
                values |= {p for v in list(values) for p in re.split(r"\s+", v) if len(p) >= 4}
            for value in sorted(values, key=len, reverse=True):
                body = r"\s+".join(re.escape(p) for p in re.split(r"\s+", _fold(value)))
                self._lookup.append((kind, re.compile(rf"(?<!\w){body}(?!\w)")))

    def find(self, text: str) -> list[Span]:
        """All sensitive spans, overlaps resolved (earlier detector, then longer, wins)."""
        candidates: list[tuple[int, Span]] = []
        rank = 0
        for kind, pattern, validator, group in _DETECTORS[:4]:
            for m in pattern.finditer(text):
                if validator is None or validator(m.group(group)):
                    candidates.append((rank, Span(m.start(group), m.end(group), kind, m.group(group))))
            rank += 1
        folded = _fold(text)
        for kind, pattern in self._lookup:
            for m in pattern.finditer(folded):
                candidates.append((rank, Span(m.start(), m.end(), kind, text[m.start():m.end()])))
        rank += 1
        for kind, pattern, validator, group in _DETECTORS[4:]:
            for m in pattern.finditer(text):
                if validator is None or validator(m.group(group)):
                    candidates.append((rank, Span(m.start(group), m.end(group), kind, m.group(group))))
            rank += 1
        accepted: list[Span] = []
        for _, span in sorted(candidates, key=lambda c: (c[0], -(c[1].end - c[1].start), c[1].start)):
            if all(span.end <= a.start or span.start >= a.end for a in accepted):
                accepted.append(span)
        return sorted(accepted, key=lambda s: s.start)

    def redact(self, text: str, vault: Vault) -> Redacted:
        spans = self.find(text)
        out, pos, by_kind = [], 0, {}
        for s in spans:
            out.append(text[pos:s.start])
            out.append(vault.placeholder_for(s.kind, _canonical(s.kind, s.value), s.value))
            pos = s.end
            by_kind[s.kind] = by_kind.get(s.kind, 0) + 1
        out.append(text[pos:])
        result = "".join(out)
        return Redacted(result, RedactionReport(len(text), len(result), len(spans), by_kind))

    def redact_or_block(self, text: str, vault: Vault) -> Redacted:
        """Redact, then re-scan the output. Anything still detectable means the
        call is blocked (fail closed), never sent partly redacted."""
        try:
            result = self.redact(text, vault)
            leftover = self.find(result.text)
        except Exception as exc:  # any failure in redaction blocks the call
            raise TierBlocked("redaction failed") from exc
        if leftover:
            raise TierBlocked(f"redaction incomplete ({len(leftover)} identifier(s) remain)")
        return result


def _canonical(kind: str, value: str) -> str:
    """Same identifier written two ways (spaced / unspaced) -> one placeholder."""
    if kind in ("IBAN", "NIB", "CARD", "NIF", "PHONE"):
        return re.sub(r"[\s-]", "", value).upper()
    if kind == "EMAIL":
        return value.lower()
    return " ".join(_fold(value).split())
