"""Health's contribution to Ask. Every tool reports tier=HIGH even when nothing matches: calling one IS
touching Health, so the engine raises the run to High before the next model call (decision D18)."""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.categories.health import areas as areas_svc
from app.categories.health.kinds import KIND_LABELS
from app.categories.health.text import match_key, reference_for_display
from app.models.document import Document
from app.models.category import Category
from app.models.health import HealthEntry, HealthMeasurement, HealthPerson
from app.privacy.reach import reach_for
from app.privacy.tiers import Tier
from app.services.ask.contracts import AskTool, ToolOutput
from app.services.ask.refs import citable_for_document
from app.services.ask.tools import settled_entries

_LIMIT = 40


def _readable_entries(session: Session) -> list[HealthEntry]:
    reach = reach_for(session)
    ids = {e.document.id for e in settled_entries(session, Category.HEALTH, reach=reach) if e.document is not None}
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
        lines.append(" | ".join(part for part in [
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
    # One data point per (person, test, date, value): a later paper reprints earlier results as history, and the same
    # point must not be counted twice. The paper that measured it (not a reprint) is the one cited.
    best: dict[tuple, tuple] = {}
    for row in rows:
        when, m, e = row
        key = (m.person_id, m.test_key, when, m.value, (m.value_text or "").casefold())
        if key not in best or (best[key][1].is_previous and not m.is_previous):
            best[key] = row
    rows = sorted(best.values(), key=lambda r: (r[0], r[1].position))
    lines = [f"{len(rows)} measurements, oldest first:"]
    docs = []
    for when, m, e in rows[:200]:
        person = people.get(m.person_id)
        value = f"{m.value:g} {m.unit or ''}".strip() if m.value is not None else (m.value_text or "?")
        shown_ref = reference_for_display(m.reference_text, m.ref_low, m.ref_high)
        rng = f" (reference {shown_ref})" if shown_ref else ""
        flag = ""
        if m.out_of_range is True:
            flag = " — above range" if m.ref_high is not None and m.value is not None and m.value > m.ref_high else " — below range"
        check = " [doubtful reading]" if m.needs_check else ""
        lines.append(f"{when if when != date.min else 'undated'} | {person.name if person else 'unknown'} | "
                     f"{m.group_path + ' › ' if m.group_path else ''}{m.test}{' (printed as history on that paper)' if m.is_previous else ''}"
                     f" | {value}{rng}{flag}{check} | doc:{e.document_id}")
        docs.append(e.document_id)
    cites = [citable_for_document(d) for d in session.exec(select(Document).where(Document.id.in_(set(docs)))).all()] if docs else []
    out = _output("\n".join(lines), len(rows[:200]), cites)
    out.visual = _measurement_visual(rows)
    return out


def _measurement_visual(rows: list) -> Optional[dict]:
    """One person's one test over time, as the person page draws it: the range printed on the latest paper."""
    points = [(when, m) for when, m, _e in rows if m.value is not None and when != date.min]
    if len(points) < 2 or len({(m.person_id, m.test_key) for _w, m in points}) != 1:
        return None
    points = points[-8:]
    last = points[-1][1]
    ref = [last.ref_low, last.ref_high] if (last.ref_low is not None or last.ref_high is not None) else None
    return {"kind": "columns", "fmt": "plain", "colour": "e", "ref": ref, "title": f"{last.test} over time",
            "items": [{"label": f"{w:%Y-%m}", "value": m.value, "current": i == len(points) - 1}
                      for i, (w, m) in enumerate(points)]}


_PERSON = {"type": "string", "description": "Part of the person's name."}
_YEAR = {"type": "integer"}

def _conditions(session: Session, args: dict) -> ToolOutput:
    """Step 7d: each person's conditions with their status, papers, next follow-up, medication, visits and notes.
    A condition is shown only when the AI may read one of its person's papers (fail closed); its papers are only the
    readable ones."""
    from app.categories.health import care, conditions as cs, follow_ups as fu
    from app.models.health import HealthCondition, HealthNote
    people = _people(session)
    readable = {e.id: e for e in _readable_entries(session)}
    readers = {e.person_id for e in readable.values()}
    wanted = match_key(args.get("condition") or "")
    today = date.today()
    lines, citables, count = [], [], 0
    for c in session.exec(select(HealthCondition).order_by(HealthCondition.person_id, HealthCondition.name)).all():
        person = people.get(c.person_id)
        if not _person_ok(person, args.get("person")) or (wanted and wanted not in c.match_key):
            continue
        papers = [e for e in cs.papers_of(session, c.id) if e.id in readable]
        if not papers and c.person_id not in readers:
            continue                                  # nothing of this person may be read: the condition stays out
        count += 1
        docs = [session.get(Document, e.document_id) for e in papers]
        citables += [citable_for_document(d) for d in docs if d is not None]
        nxt = fu.open_ones(session, today, condition_id=c.id)
        meds = [m for m in care.medications(session, c.id) if m.stopped_on is None]
        booked = [v for v in care.visits(session, c.id, "booked") if v.visit_on >= today]
        done = [v for v in care.visits(session, c.id, "done")]
        notes = session.exec(select(HealthNote).where(HealthNote.condition_id == c.id)
                             .order_by(HealthNote.noted_on.desc())).all()[:5]
        lines.append(" | ".join(part for part in [
            f"{person.name if person else 'unknown person'}: {c.name}", f"status {c.status}",
            f"since {c.started_on}" if c.started_on else "start not recorded",
            f"followed by {c.followed_by}" if c.followed_by else "",
            "papers: " + (", ".join(f"doc:{e.document_id} {e.entry_date or 'undated'}" for e in papers) or "none readable"),
            f"next follow-up {nxt[0].follow_up.due_on}: {nxt[0].follow_up.what}" if nxt else "no follow-up set",
            "medication: " + (", ".join(" ".join(filter(None, [m.name, m.dose, m.frequency])) for m in meds) or "none"),
            f"next visit {booked[0].visit_on}" if booked else "",
            f"last visit {done[-1].visit_on}: {done[-1].observations or ''} {done[-1].plan_changes or ''}".strip()
            if done else "",
            "notes: " + "; ".join(f"{n.noted_on} {n.kind} {n.value if n.value is not None else ''} {n.text}".strip()
                                  for n in notes) if notes else ""] if part))
    text = (f"{count} condition{'s' if count != 1 else ''} (private Health):\n" + "\n".join(lines)
            if lines else "No condition is recorded for that person.")
    return _output(text, count, citables)


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
                     "Includes the earlier results that newer papers print for comparison (history columns), "
                     "each with its own date, so one call gives the whole history of a test. "
                     "Use for trends. Using this makes the whole answer private."),
        input_schema={"type": "object", "properties": {
            "person": _PERSON, "test": {"type": "string"},
            "date_from": {"type": "string", "description": "YYYY-MM-DD"}, "date_to": {"type": "string", "description": "YYYY-MM-DD"}}},
        run=_measurements),
    AskTool(
        name="health_conditions",
        description=("PRIVATE. The family's long-running conditions per person, as the family recorded them: status "
                     "(followed, stable, resolved), since when, who follows it, the papers confirmed to be about it "
                     "(doc:ID to cite), the next follow-up, current medication, visits and notes. Filter by person or "
                     "condition. Using this makes the whole answer private."),
        input_schema={"type": "object", "properties": {"person": _PERSON, "condition": {"type": "string"}}},
        run=_conditions),
)
