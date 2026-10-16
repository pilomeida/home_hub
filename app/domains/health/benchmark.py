"""The Health benchmark: real papers with the reading a person has confirmed as right, re-read and compared before
any change to the reader goes live (Pedro, 2026-10-16: first time right, not back and forth).

A case is a folder under data/health_benchmark/ (git-ignored: these are real health papers):
    <case>/paper.pdf|png|jpg     the paper
    <case>/expected.json         the confirmed reading (written by `promote` from a paper in the Hub, then reviewed)
`compare` is pure (testable offline); the command that runs the cases against the real model is
scripts/health_benchmark.py."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from app.domains.fields import load_fields
from app.domains.health import store
from app.domains.health.extraction import ExtractedHealth, ExtractedMeasurement
from app.domains.health.text import PATH_SEP, match_key, spaced_intervals
from app.models.document import Document
from app.models.health import HealthMeasurement, HealthPerson

DEFAULT_DIR = Path("data/health_benchmark")
PAPER_SUFFIXES = (".pdf", ".png", ".jpg", ".jpeg", ".webp")


def _key(path: str, test: str, unit: Optional[str]) -> str:
    return "|".join((match_key(path or ""), match_key(test), match_key(unit or "")))


def _same_number(a: Optional[float], b: Optional[float]) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) < 1e-6)


def _same_text(a: Optional[str], b: Optional[str]) -> bool:
    return match_key(a or "") == match_key(b or "")


def _earlier(rows: list[dict]) -> set[tuple]:
    return {(r.get("date") or "", None if r.get("value") is None else round(float(r["value"]), 6), match_key(r.get("value_text") or ""))
            for r in rows or []}


@dataclass
class CaseResult:
    name: str
    problems: list[str] = field(default_factory=list)     # each one fails the case
    notes: list[str] = field(default_factory=list)        # information: extra results, the reading's own check notes
    expected: int = 0
    found: int = 0

    @property
    def passed(self) -> bool:
        return not self.problems


def expected_from_reading(x: ExtractedHealth) -> dict:
    """The expected.json shape of a reading (used by tests; `promote` builds the same shape from stored rows)."""
    previous: dict[int, list[dict]] = {}
    for m in x.measurements:
        if m.is_previous:
            previous.setdefault(m.position, []).append(
                {"date": m.measured_on.isoformat() if m.measured_on else None, "value": m.value, "value_text": m.value_text})
    results = [{"path": m.group_path, "test": m.test, "unit": m.unit, "value": m.value, "value_text": m.value_text,
                "reference": m.reference_text, "previous": previous.get(m.position, [])}
               for m in x.measurements if not m.is_previous]
    return {"header": {"category": x.category, "document_date": x.document_date.isoformat() if x.document_date else None,
                       "person": x.person_name, "provider": x.provider}, "results": results}


def compare(name: str, expected: dict, actual: ExtractedHealth) -> CaseResult:
    out = CaseResult(name)
    head = expected.get("header") or {}
    if head.get("category") and head["category"] != actual.category:
        out.problems.append(f"kind of paper: expected {head['category']}, read {actual.category}")
    if head.get("document_date") and head["document_date"] != (actual.document_date.isoformat() if actual.document_date else None):
        out.problems.append(f"date: expected {head['document_date']}, read {actual.document_date}")
    if head.get("person") and not _same_text(head["person"], actual.person_name):
        out.problems.append(f"person: expected {head['person']}, read {actual.person_name}")
    if head.get("provider") and match_key(head["provider"]) not in match_key(actual.provider or "") \
            and match_key(actual.provider or "") not in match_key(head["provider"]):
        out.problems.append(f"provider: expected {head['provider']}, read {actual.provider}")

    current = [m for m in actual.measurements if not m.is_previous]
    earlier_of: dict[int, list[ExtractedMeasurement]] = {}
    for m in actual.measurements:
        if m.is_previous:
            earlier_of.setdefault(m.position, []).append(m)
    by_full, by_test = {}, {}
    for m in current:
        by_full.setdefault(_key(m.group_path, m.test, m.unit), []).append(m)
        by_test.setdefault(_key("", m.test, m.unit), []).append(m)
    used: set[int] = set()
    results = expected.get("results") or []
    out.expected = len(results)
    for e in results:
        label = f"{e.get('path') or '(no heading)'} › {e['test']}" + (f" ({e['unit']})" if e.get("unit") else "")
        pool = [m for m in by_full.get(_key(e.get("path") or "", e["test"], e.get("unit")), []) if id(m) not in used]
        wrong_group = False
        if not pool:
            pool = [m for m in by_test.get(_key("", e["test"], e.get("unit")), []) if id(m) not in used]
            wrong_group = bool(pool)
        if not pool:
            out.problems.append(f"missing: {label}")
            continue
        m = pool[0]
        used.add(id(m))
        out.found += 1
        if wrong_group:
            out.problems.append(f"wrong heading: {label} was read under '{m.group_path or '(no heading)'}'")
        if not (_same_number(e.get("value"), m.value) and _same_text(e.get("value_text"), m.value_text)):
            out.problems.append(f"wrong value: {label}: expected {e.get('value') if e.get('value') is not None else e.get('value_text')}, "
                                f"read {m.value if m.value is not None else m.value_text}")
        if spaced_intervals(e.get("reference") or "") != spaced_intervals(m.reference_text or ""):
            out.problems.append(f"wrong reference: {label}: expected '{e.get('reference')}', read '{m.reference_text}'")
        got_earlier = _earlier([{"date": p.measured_on.isoformat() if p.measured_on else None, "value": p.value,
                                 "value_text": p.value_text} for p in earlier_of.get(m.position, [])])
        if _earlier(e.get("previous") or []) != got_earlier:
            out.problems.append(f"wrong earlier results: {label}")
    extras = [m for m in current if id(m) not in used]
    if extras:
        out.notes.append(f"{len(extras)} result(s) read that the case does not list: "
                         + "; ".join(f"{m.group_path} › {m.test}" for m in extras[:8]) + (" ..." if len(extras) > 8 else ""))
    out.notes.extend(f"the reading's own check: {n}" for n in actual.check_notes)
    return out


def load_cases(directory: Path = DEFAULT_DIR, only: Optional[str] = None) -> list[tuple[str, Path, dict]]:
    cases = []
    for folder in sorted(p for p in directory.iterdir() if p.is_dir()) if directory.is_dir() else []:
        if only and folder.name != only:
            continue
        paper = next((p for p in sorted(folder.iterdir()) if p.suffix.lower() in PAPER_SUFFIXES), None)
        expected = folder / "expected.json"
        if paper is not None and expected.is_file():
            cases.append((folder.name, paper, json.loads(expected.read_text(encoding="utf-8"))))
    return cases


def promote(session: Session, document_id: int, name: str, directory: Path = DEFAULT_DIR) -> Path:
    """Turn a paper in the Hub, as it now stands (after a person has corrected it), into a benchmark case."""
    document = session.get(Document, document_id)
    entry = store.entry_for(session, document_id) if document else None
    if document is None or entry is None:
        raise ValueError(f"Document {document_id} is not a read health paper.")
    rows = list(session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)
                             .order_by(HealthMeasurement.position, HealthMeasurement.is_previous, HealthMeasurement.measured_on)))
    person = session.get(HealthPerson, entry.person_id) if entry.person_id else None
    earlier: dict[int, list[dict]] = {}
    for m in rows:
        if m.is_previous:
            earlier.setdefault(m.position, []).append(
                {"date": m.measured_on.isoformat() if m.measured_on else None, "value": m.value, "value_text": m.value_text})
    expected = {
        "reviewed": False,         # set to true once a person has checked every line against the paper
        "header": {"category": document.category, "document_date": entry.entry_date.isoformat() if entry.entry_date else None,
                   "person": person.name if person else None, "provider": entry.provider},
        "results": [{"path": m.group_path, "test": m.test, "unit": m.unit, "value": m.value, "value_text": m.value_text,
                     "reference": m.reference_text, "previous": earlier.get(m.position, [])} for m in rows if not m.is_previous],
    }
    folder = directory / name
    folder.mkdir(parents=True, exist_ok=True)
    source = Path(document.file_path)
    if not source.is_absolute():
        source = Path.cwd() / source
    shutil.copyfile(source, folder / f"paper{source.suffix.lower()}")
    (folder / "expected.json").write_text(json.dumps(expected, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return folder
