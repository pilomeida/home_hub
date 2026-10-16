"""Health's contribution to Ask. Every tool reports tier=HIGH even when nothing matches: calling one IS
touching Health, so the engine raises the run to High before the next model call (decision D18)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.domains.health import areas as areas_svc
from app.domains.health.categories import KIND_LABELS
from app.domains.health.text import match_key
from app.models.document import Document
from app.models.domain import Domain
from app.models.health import HealthEntry, HealthMeasurement, HealthPerson
from app.privacy.reach import reach_for
from app.privacy.tiers import Tier
from app.services.ask.contracts import AskTool, ToolOutput
from app.services.ask.refs import citable_for_document
from app.services.ask.tools import settled_entries

_LIMIT = 40


def _readable_entries(session: Session) -> list[HealthEntry]:
    reach = reach_for(session)
    ids = {e.document.id for e in settled_entries(session, Domain.HEALTH, reach=reach) if e.document is not None}
    if not ids:
        return []
    return list(session.exec(select(HealthEntry).where(HealthEntry.document_id.in_(ids))).all())


def _people(session: Session) -> dict[int, HealthPerson]:
    return {p.id: p for p in session.exec(select(HealthPerson).where(HealthPerson.merged_into_id.is_(None))).all()}


def _person_ok(person: Optional[HealthPerson], wanted: Optional[str]) -> bool:
    return not wanted or (person is not None and match_key(wanted) in person.match_key)


def _tags(session: Session, entry: HealthEntry):
    return areas_svc.tags_of(session, entry.id)


def _output(text: str, count: int, citables=()) -> ToolOutput:
    return ToolOutput(text=text, citables=list(citables), used_raw_sources=True, tier=Tier.HIGH,
                      opened=f"{count} private Health record{'s' if count != 1 else ''}")


def _find_records(session: Session, args: dict) -> ToolOutput:
    people = _people(session)
    query = match_key(args.get("query") or "")
    area, condition = match_key(args.get("area") or ""), match_key(args.get("condition") or "")
    limit = max(1, min(int(args.get("limit", _LIMIT)), 60))
    rows = []
    for e in _readable_entries(session):
        if not _person_ok(people.get(e.person_id), args.get("person")):
            continue
        if args.get("year") and e.year != int(args["year"]):
            continue
        if args.get("kind") and e.kind != args["kind"]:
            continue
        tags = _tags(session, e)
        if area and not any(t.kind == "area" and area in t.match_key for t in tags):
            continue
        if condition and not any(t.kind == "condition" and condition in t.match_key for t in tags):
            continue
        doc = session.get(Document, e.document_id)
        hay = match_key(" ".join([doc.filename, e.provider or "", e.summary or "", e.full_text or "",
                                  " ".join(t.name for t in tags)]))
        if query and not all(tok in hay for tok in query.split()):
            continue
        rows.append((e, doc, tags))
    rows.sort(key=lambda r: (r[0].entry_date or date.min), reverse=True)
    shown = rows[:limit]
    lines = [f"{len(rows)} matching private Health records (showing {len(shown)}):"]
    for e, doc, tags in shown:
        person = people.get(e.person_id)
        lines.append(" | ".join([
            f"doc:{doc.id}", str(e.entry_date or "undated"), person.name if person else "unknown person",
            KIND_LABELS.get(e.kind or "", e.kind or "-"), e.provider or "-",
            "areas: " + (", ".join(t.name for t in tags if t.kind == "area") or "-"),
            "conditions: " + (", ".join(t.name for t in tags if t.kind == "condition") or "-"),
            (e.summary or "no summary")]))
    return _output("\n".join(lines), len(shown), [citable_for_document(d) for _, d, _ in shown])


def _measurements(session: Session, args: dict) -> ToolOutput:
    people = _people(session)
    test = match_key(args.get("test") or "")
    allowed = {e.id: e for e in _readable_entries(session)}
    start = date.fromisoformat(args["date_from"]) if args.get("date_from") else None
    end = date.fromisoformat(args["date_to"]) if args.get("date_to") else None
    rows = []
    for m in session.exec(select(HealthMeasurement)).all():
        e = allowed.get(m.entry_id)
        if e is None or not _person_ok(people.get(m.person_id), args.get("person")):
            continue
        if test and test not in m.test_key:
            continue
        when = m.measured_on or e.entry_date
        if (start and (when is None or when < start)) or (end and (when is None or when > end)):
            continue
        rows.append((when or date.min, m, e))
    rows.sort(key=lambda r: r[0])
    lines = [f"{len(rows)} measurements, oldest first:"]
    docs = []
    for when, m, e in rows[:200]:
        person = people.get(m.person_id)
        value = f"{m.value:g} {m.unit or ''}".strip() if m.value is not None else (m.value_text or "?")
        rng = f" (reference {m.ref_low if m.ref_low is not None else ''}–{m.ref_high if m.ref_high is not None else ''})" \
            if (m.ref_low is not None or m.ref_high is not None) else ""
        flag = ""
        if m.out_of_range is True:
            flag = " — above range" if m.ref_high is not None and m.value is not None and m.value > m.ref_high else " — below range"
        check = " [doubtful reading]" if m.needs_check else ""
        lines.append(f"{when if when != date.min else 'undated'} | {person.name if person else 'unknown'} | "
                     f"{m.test} | {value}{rng}{flag}{check} | doc:{e.document_id}")
        docs.append(e.document_id)
    cites = [citable_for_document(d) for d in session.exec(select(Document).where(Document.id.in_(set(docs)))).all()] if docs else []
    return _output("\n".join(lines), len(rows[:200]), cites)


def _spending(session: Session, args: dict) -> ToolOutput:
    people = _people(session)
    totals: dict[tuple[str, int], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0])
    for e in _readable_entries(session):
        person = people.get(e.person_id)
        if not _person_ok(person, args.get("person")):
            continue
        if args.get("year") and e.year != int(args["year"]):
            continue
        t = totals[(person.name if person else "unknown", e.year or 0)]
        t[0] += e.amount_billed or 0.0
        t[1] += e.amount_reimbursed or 0.0
        t[2] += e.amount_owed or 0.0
        t[3] += 1
    lines = ["Health spending (EUR) per person and year, from the papers on file:"]
    for (name, year), (billed, back, owed, n) in sorted(totals.items()):
        lines.append(f"{name} | {year or 'undated'} | billed {billed:.2f} | reimbursed {back:.2f} | "
                     f"still owed {owed:.2f} | {n} papers")
    if len(lines) == 1:
        lines.append("(nothing on file)")
    return _output("\n".join(lines), sum(int(t[3]) for t in totals.values()))


_PERSON = {"type": "string", "description": "Part of the person's name."}
_YEAR = {"type": "integer"}

HEALTH_TOOLS = (
    AskTool(
        name="health_find_records",
        description=("PRIVATE. Find the family's health papers by person, year, kind (check_up, follow_up, test, "
                     "procedure, emergency, dental, vaccination, other), body area, condition or words. Returns "
                     "date, person, provider, areas, conditions, summary and doc:ID to cite. Using this makes the "
                     "whole answer private."),
        input_schema={"type": "object", "properties": {
            "person": _PERSON, "year": _YEAR, "kind": {"type": "string"}, "area": {"type": "string"},
            "condition": {"type": "string"}, "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 60}}},
        run=_find_records),
    AskTool(
        name="health_measurements",
        description=("PRIVATE. Measured values (lab values, blood pressure, weight...) oldest first, with unit, "
                     "reference range and whether the reading was doubtful. Filter by person, test name and dates. "
                     "Use for trends. Using this makes the whole answer private."),
        input_schema={"type": "object", "properties": {
            "person": _PERSON, "test": {"type": "string"},
            "date_from": {"type": "string", "description": "YYYY-MM-DD"}, "date_to": {"type": "string", "description": "YYYY-MM-DD"}}},
        run=_measurements),
    AskTool(
        name="health_spending",
        description=("PRIVATE. What health costs: billed, reimbursed and still owed (EUR) per person and year, from "
                     "medical bills and reimbursement statements on file. Using this makes the whole answer private."),
        input_schema={"type": "object", "properties": {"person": _PERSON, "year": _YEAR}},
        run=_spending),
)
