"""Checking a reading against the paper it came from (Pedro, 2026-10-16: first time right, not back and forth).

The model reads; this checks, mechanically and at no AI cost, that the reading and the paper agree:
  * FIDELITY  every number the reading states as a value is printed on the paper (a transposed or invented
              value is caught and that result is marked doubtful);
  * COVERAGE  every number printed on the paper is accounted for somewhere in the reading (a dropped row is caught
              and one targeted second pass asks the model about exactly those lines).
It needs the paper's own text, so it applies to a PDF that carries text; a scan or photo has nothing independent to
check against and is left to a person."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

from app.domains.health.extraction import ExtractedHealth, ExtractedMeasurement
from app.domains.health.text import PATH_SEP, match_key
from app.domains.health.topics import topic_path

# A number as a lab prints it: 5,54  16,0  1.020  -3  (a '-' is a sign only when it does not follow a digit or letter,
# so a range 135-150 or a date 10-05-2021 gives plain numbers; digits inside a word or a unit such as x10^9/L's 10
# are not numbers of their own, those belong to the unit and are matched through the unit text).
_TOKEN = re.compile(r"(?<![\w.,/])-?\d+(?:[.,]\d+)?(?![\w])")
_FURNITURE = re.compile(r"^\s*(?:p[aá]g(?:ina)?\.?|page)\s*\d+\s*(?:/|de|of)\s*\d+\s*$", re.IGNORECASE)
# A line about money (billing is not Health's business, so it is neither read nor asked about). Checked on the
# folded text (no accents, lower case). "total" alone is NOT a cue: "Colesterol total" is a result.
_MONEY_LINE = re.compile(r"€|\beur\b|taxa moderadora|\ba pagar\b|\bpago\b|\bpagamento\b|reembols", re.IGNORECASE)
MAX_LINES_TO_ASK = 40


def numbers_in(text: Optional[str]) -> list[float]:
    out: list[float] = []
    for raw in _TOKEN.findall(text or ""):
        try:
            out.append(round(float(raw.replace(",", ".")), 6))
        except ValueError:
            continue
    return out


@dataclass
class Finding:
    kind: str                       # "not_on_paper" | "not_captured"
    message: str                    # plain words, for the person
    test: Optional[str] = None
    position: Optional[int] = None


@dataclass
class CheckReport:
    findings: list[Finding] = field(default_factory=list)
    uncaptured_lines: list[str] = field(default_factory=list)       # paper lines with numbers the reading has nowhere

    @property
    def clean(self) -> bool:
        return not self.findings and not self.uncaptured_lines


def _strings_of(x: ExtractedHealth) -> Iterable[Optional[str]]:
    yield from (x.person_name, x.provider, x.provider_phone, x.provider_address, x.summary, x.details,
                x.indication, x.clinical_notes, x.requested_by, x.performed_by,
                x.appointment_time, *x.areas, *x.conditions)
    for m in x.measurements:
        yield from (m.test, m.group_path, m.value_text, m.unit, m.reference_text, m.method)


def _dates_of(x: ExtractedHealth) -> Iterable[Optional[date]]:
    yield from (x.document_date, x.appointment_date)
    yield from (m.measured_on for m in x.measurements)


def accounted_numbers(x: ExtractedHealth) -> set[float]:
    """Every number the reading holds anywhere: values, limits, dates, and the numbers inside its text."""
    have: set[float] = set()
    for s in _strings_of(x):
        have.update(numbers_in(s))
    for m in x.measurements:
        have.update(round(v, 6) for v in (m.value, m.ref_low, m.ref_high) if v is not None)
    for d in _dates_of(x):
        if d is not None:
            have.update((float(d.year), float(d.month), float(d.day)))
    return have


def _lines_with(numbers: set[float], source: str) -> list[str]:
    lines, seen = [], set()
    for line in source.splitlines():
        text = re.sub(r"\s+", " ", line).strip()
        if not text or text in seen or _FURNITURE.match(text) or _MONEY_LINE.search(match_key(text)):
            continue
        if any(n not in numbers for n in numbers_in(text)):
            lines.append(text)
            seen.add(text)
    return lines


def check_reading(x: ExtractedHealth, source_text: Optional[str]) -> CheckReport:
    """Compare the reading with the paper's own text. Marks a result `uncertain` when its value is not on the paper."""
    report = CheckReport()
    if not source_text or not source_text.strip():
        return report                                                 # a scan: nothing independent to check against
    printed = set(numbers_in(source_text))
    for m in x.measurements:
        wrong = [v for v in (m.value,) if v is not None and round(v, 6) not in printed]
        if wrong:
            m.uncertain = m.uncertain or not m.is_previous        # an earlier value has no button of its own: it is a note
            when = f" (earlier result of {m.measured_on:%Y-%m-%d})" if m.is_previous and m.measured_on else ""
            report.findings.append(Finding(
                "not_on_paper", f"{m.test}{when}: the value {wrong[0]:g} is not printed on the paper.", m.test, m.position))
    have = accounted_numbers(x)
    report.uncaptured_lines = _lines_with(have, source_text)
    return report


def unresolved_notes(report: CheckReport) -> list[str]:
    """What is still doubtful once the targeted second pass has run, in plain words."""
    notes = [f.message for f in report.findings]
    notes += [f"Printed on the paper but not found in what was read: \"{line}\"" for line in report.uncaptured_lines]
    return notes


# --- two independent readings of the same paper ----------------------------------------------------------------

MAX_NOTES_PER_KIND = 5
SAME_WORDING_AT_LEAST = 0.85


def _rows_by_key(x: ExtractedHealth) -> dict[tuple, tuple[str, ExtractedMeasurement]]:
    """The reading's own results (not earlier ones restated), keyed by (topic, test, unit) and, when the same key
    is printed twice, by how many times it was seen so far; each with the path to show a person."""
    rows: dict[tuple, tuple[str, ExtractedMeasurement]] = {}
    seen: dict[tuple, int] = {}
    for m in x.measurements:
        if m.is_previous:
            continue
        names = topic_path(m.group_path)
        path = PATH_SEP.join(names)
        base = (match_key(path), match_key(m.test), match_key(m.unit or ""))
        seen[base] = seen.get(base, 0) + 1
        rows[base + (seen[base],)] = (path, m)
    return rows


def _where(path: str, test: str) -> str:
    return f"{path}{PATH_SEP}{test}" if path else test


def compare_readings(a: ExtractedHealth, b: ExtractedHealth) -> list[str]:
    """Where two independent readings of one paper disagree, in plain words (at most 5 notes of each kind)."""
    ra, rb = _rows_by_key(a), _rows_by_key(b)
    only: list[str] = []
    values: list[str] = []
    lopsided: list[str] = []
    wording: list[str] = []
    for key in [*ra, *(k for k in rb if k not in ra)]:
        if key in ra and key in rb:
            (path, x), (_, y) = ra[key], rb[key]
            if x.value is not None and y.value is not None:
                if abs(x.value - y.value) > 1e-6:
                    values.append(f"Two readings gave different values for {_where(path, x.test)}: {x.value:g} and {y.value:g}.")
            elif x.value is not None or y.value is not None:        # a number in one, words or nothing in the other
                one, other = (x, y) if x.value is not None else (y, x)
                found = (other.value_text or "").strip() or "no value"
                lopsided.append(f"Two readings disagree about {_where(path, x.test)}: one found {one.value:g}, "
                                f"the other found {found}. Look at the paper.")
            elif x.value_text and y.value_text:
                ta, tb = match_key(x.value_text), match_key(y.value_text)
                if ta != tb and difflib.SequenceMatcher(None, ta, tb).ratio() < SAME_WORDING_AT_LEAST:
                    note = f"The wording read under {path or x.test} differs between two readings. Look at the paper."
                    if note not in wording:
                        wording.append(note)
        else:
            path, m = (ra[key] if key in ra else rb[key])
            only.append(f"Only one of two readings found: {_where(path, m.test)}. Look at the paper.")
    notes = only[:MAX_NOTES_PER_KIND] + values[:MAX_NOTES_PER_KIND] + lopsided[:MAX_NOTES_PER_KIND] + wording[:MAX_NOTES_PER_KIND]
    if a.document_date != b.document_date:
        notes.append("The two readings disagree about the date of the paper. Look at the paper.")
    if match_key(a.person_name or "") != match_key(b.person_name or ""):
        notes.append("The two readings disagree about whose paper it is. Look at the paper.")
    return notes
