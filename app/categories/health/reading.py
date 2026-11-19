"""Reading a health paper, first time right: read, check against the paper, ask once about exactly what is missing.

extract_health_document (extraction.py) is the one model call that reads the whole paper. This wraps it:
the reading is checked mechanically against the paper's own text (checks.py); if printed numbers are missing from
it, ONE short second call shows the model just those lines; what is still doubtful afterwards is recorded as
plain-words notes on the paper (never a silently wrong table)."""

from __future__ import annotations

import json
from typing import Optional, Sequence

from app.categories.health import extraction as ex
from app.categories.health.layout import apply_layout
from app.categories.health.checks import MAX_LINES_TO_ASK, CheckReport, check_reading, compare_readings, unresolved_notes
from app.llm_gateway import HEALTH_EXTRACTION, get_gateway
from app.privacy.tiers import Tier
from app.services.checks import Doubt, from_doubts, from_health, read_twice
from app.services.json_utils import strip_json_fences

_RESULTS_RULES = ex._SYSTEM[ex._SYSTEM.index("- sections:"):ex._SYSTEM.index("{full_text_rule}")]

_REPAIR_SYSTEM = (
    "You are completing the reading of a medical paper (Portuguese or English). Some lines printed on the paper were "
    "not captured. For EACH line given: if it is a result, return it in sections, under the heading path it belongs "
    "to (reuse one of the existing headings listed when it fits); if it is other printed information "
    "(identification, dates, notes, accreditation, address), return it in details as \"Label: value\" lines; if it "
    "is only page furniture (a page number, a barcode), ignore it. Leave out prices, fees, co-payments (taxa moderadora), "
    "totals, payments and reimbursements: billing is handled elsewhere. Never invent anything.\n"
    "Return ONLY a JSON object {\"sections\": [...], \"details\": \"...\"}, leaving out what does not apply.\n\n"
    + _RESULTS_RULES)

_REPAIR_SCHEMA = {
    "type": "object",
    "properties": {"sections": ex._SCHEMA["properties"]["sections"], "details": ex._SCHEMA["properties"]["details"]},
}

MAX_NOTES = 10


async def _second_look(x: ex.ExtractedHealth, lines: Sequence[str], gateway) -> None:
    """Show the model only the lines the reading has no place for; merge what it returns into the reading."""
    headings = sorted({m.group_path for m in x.measurements if m.group_path})
    user = ("Existing headings:\n" + "\n".join(f"- {h}" for h in headings) + "\n\n"
            "Lines of the paper that are not in the reading:\n" + "\n".join(f"- {line}" for line in lines[:MAX_LINES_TO_ASK]))
    result = await gateway.run(HEALTH_EXTRACTION, _REPAIR_SYSTEM, user, sensitivity=Tier.HIGH.value,
                               response_schema=_REPAIR_SCHEMA)
    data = json.loads(strip_json_fences(result.text))
    if not isinstance(data, dict):
        raise ValueError("not an object")
    last = max((m.position for m in x.measurements), default=0)
    for m in ex._measurements(data):
        m.position += last                                   # after everything already read; the heading path keeps it grouped
        x.measurements.append(m)
    more = ex._text(data.get("details"))
    if more:
        x.details = (x.details + "\n" + more).strip()


def drop_wrappers(x: ex.ExtractedHealth) -> None:
    """A paper's headings without the wrapper that opens them ("Conclusões › Triplex carotídeo" -> "Triplex
    carotídeo"): the result's own name ("Conclusão") already says what it is, and topics drop it too."""
    from app.categories.health.topics import topic_path
    for m in x.measurements:
        if m.group_path:
            kept = topic_path(m.group_path)
            if kept:                                          # a result whose only heading is the wrapper keeps it
                m.group_path = ex.PATH_SEP.join(kept)


def complete_from(x: ex.ExtractedHealth, y: ex.ExtractedHealth) -> None:
    """Where one reading cut a written finding short and the other has it whole (the first is the start of the
    second), the whole one is kept."""
    from app.categories.health.text import match_key
    longer = {(match_key(m.group_path), match_key(m.test)): m.value_text for m in y.measurements if m.value_text}
    for m in x.measurements:
        other = longer.get((match_key(m.group_path), match_key(m.test)))
        if m.value_text and other and len(other) > len(m.value_text) \
                and match_key(other).startswith(match_key(m.value_text).rstrip(" .")):
            m.value_text = other


def _settle(x: ex.ExtractedHealth, file_path: str) -> None:
    apply_layout(x, file_path)
    drop_wrappers(x)


async def _laid_out(reading, file_path: str) -> ex.ExtractedHealth:
    """A reading with the paper's own layout applied, so two readings are compared on the same footing."""
    y = await reading
    await ex.asyncio.to_thread(_settle, y, file_path)
    return y


async def read_health_document(
    file_path: str, paper_kind: Optional[str], *, existing_areas: Sequence[str] = (),
    existing_conditions: Sequence[str] = (), gateway=None, guidance: str = "", second_read: bool = True,
) -> ex.ExtractedHealth:
    gw = gateway or get_gateway()
    x = await ex.extract_health_document(file_path, paper_kind, existing_areas=existing_areas,
                                         existing_conditions=existing_conditions, gateway=gw, guidance=guidance)
    if x.is_bill:                                             # refused by the caller: nothing to check or compare, no more reads
        return x
    await ex.asyncio.to_thread(_settle, x, file_path)         # what the PDF's own layout settles (step 5d)
    source = await ex.asyncio.to_thread(ex.paper_text, file_path)       # None for a scan or photo: nothing to check against
    report: CheckReport = check_reading(x, source)
    doubts: list[Doubt] = []
    if report.uncaptured_lines:
        try:
            await _second_look(x, report.uncaptured_lines, gw)
        except Exception as exc:                              # the reading is still good: say what could not be re-checked
            doubts.append(Doubt(f"A second look at {len(report.uncaptured_lines)} printed line(s) did not work "
                                f"({type(exc).__name__}); they were left as read."))
        report = check_reading(x, source)                     # what is still missing, and values now on the page
    doubts += from_health(report, had_text=bool(source)).doubts
    if second_read:                                           # every paper is read twice, independently (shared step)
        doubts += await read_twice(
            lambda: _laid_out(ex.extract_health_document(file_path, paper_kind, existing_areas=existing_areas,
                                                         existing_conditions=existing_conditions, gateway=gw,
                                                         guidance=guidance), file_path),
            lambda y: (complete_from(x, y), compare_readings(x, y))[1])
    x.check = from_doubts(doubts, checked=bool(source) or second_read)     # the one check-result shape (step 5c)
    x.check_notes = x.check.notes(MAX_NOTES)
    return x
