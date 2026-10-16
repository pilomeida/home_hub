"""Checking a reading against the paper it came from (Pedro, 2026-10-16: first time right, not back and forth).

The model reads; this checks, mechanically and at no AI cost, that the reading and the paper agree:
  * FIDELITY  every number the reading states as a value is printed on the paper (a transposed or invented
              value is caught and that result is marked doubtful);
  * COVERAGE  every number printed on the paper is accounted for somewhere in the reading (a dropped row is caught
              and one targeted second pass asks the model about exactly those lines).
It needs the paper's own text, so it applies to a PDF that carries text; a scan or photo has nothing independent to
check against and is left to a person."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

from app.domains.health.extraction import ExtractedHealth

# A number as a lab prints it: 5,54  16,0  1.020  -3  (a '-' is a sign only when it does not follow a digit or letter,
# so a range 135-150 or a date 10-05-2021 gives plain numbers; digits inside a word or a unit such as x10^9/L's 10
# are not numbers of their own, those belong to the unit and are matched through the unit text).
_TOKEN = re.compile(r"(?<![\w.,/])-?\d+(?:[.,]\d+)?(?![\w])")
_FURNITURE = re.compile(r"^\s*(?:p[aá]g(?:ina)?\.?|page)\s*\d+\s*(?:/|de|of)\s*\d+\s*$", re.IGNORECASE)
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
                x.appointment_time, *x.areas, *x.conditions)
    for m in x.measurements:
        yield from (m.test, m.group_path, m.value_text, m.unit, m.reference_text, m.method)


def _dates_of(x: ExtractedHealth) -> Iterable[Optional[date]]:
    yield from (x.document_date, x.appointment_date)
    yield from (m.measured_on for m in x.measurements)


def accounted_numbers(x: ExtractedHealth) -> set[float]:
    """Every number the reading holds anywhere: values, limits, amounts, dates, and the numbers inside its text."""
    have: set[float] = set()
    for s in _strings_of(x):
        have.update(numbers_in(s))
    for m in x.measurements:
        have.update(round(v, 6) for v in (m.value, m.ref_low, m.ref_high) if v is not None)
    have.update(round(v, 6) for v in (x.amount_billed, x.amount_reimbursed, x.amount_owed) if v is not None)
    for d in _dates_of(x):
        if d is not None:
            have.update((float(d.year), float(d.month), float(d.day)))
    return have


def _lines_with(numbers: set[float], source: str) -> list[str]:
    lines, seen = [], set()
    for line in source.splitlines():
        text = re.sub(r"\s+", " ", line).strip()
        if not text or text in seen or _FURNITURE.match(text):
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
            when = f" (earlier result of {m.measured_on:%d-%m-%Y})" if m.is_previous and m.measured_on else ""
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
