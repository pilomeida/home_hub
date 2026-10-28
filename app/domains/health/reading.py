"""Reading a health paper, first time right: read, check against the paper, ask once about exactly what is missing.

extract_health_document (extraction.py) is the one model call that reads the whole paper. This wraps it:
the reading is checked mechanically against the paper's own text (checks.py); if printed numbers are missing from
it, ONE short second call shows the model just those lines; what is still doubtful afterwards is recorded as
plain-words notes on the paper (never a silently wrong table)."""

from __future__ import annotations

import json
from typing import Optional, Sequence

from app.domains.health import extraction as ex
from app.domains.health.checks import MAX_LINES_TO_ASK, CheckReport, check_reading, compare_readings, unresolved_notes
from app.llm_gateway import HEALTH_EXTRACTION, get_gateway
from app.privacy.tiers import Tier
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


async def read_health_document(
    file_path: str, category: Optional[str], *, existing_areas: Sequence[str] = (),
    existing_conditions: Sequence[str] = (), gateway=None, guidance: str = "", second_read: bool = True,
) -> ex.ExtractedHealth:
    gw = gateway or get_gateway()
    x = await ex.extract_health_document(file_path, category, existing_areas=existing_areas,
                                         existing_conditions=existing_conditions, gateway=gw, guidance=guidance)
    if x.is_bill:                                             # refused by the caller: nothing to check or compare, no more reads
        return x
    source = await ex.asyncio.to_thread(ex.paper_text, file_path)       # None for a scan or photo: nothing to check against
    report: CheckReport = check_reading(x, source)
    notes: list[str] = []
    if report.uncaptured_lines:
        try:
            await _second_look(x, report.uncaptured_lines, gw)
        except Exception as exc:                              # the reading is still good: say what could not be re-checked
            notes.append(f"A second look at {len(report.uncaptured_lines)} printed line(s) did not work ({type(exc).__name__}); "
                         "they were left as read.")
        report = check_reading(x, source)                     # what is still missing, and values now on the page
    notes += unresolved_notes(report)
    if second_read:                                           # every paper is read twice, independently
        try:
            y = await ex.extract_health_document(file_path, category, existing_areas=existing_areas,
                                                 existing_conditions=existing_conditions, gateway=gw, guidance=guidance)
            notes += compare_readings(x, y)
        except Exception as exc:                              # the first reading stands; say it was not cross-checked
            notes.append(f"A second reading to compare against did not work ({type(exc).__name__}); this reading is unchecked. Read the paper again to check it.")
    x.check_notes = notes[:MAX_NOTES] + ([f"... and {len(notes) - MAX_NOTES} more. Look at the paper."] if len(notes) > MAX_NOTES else [])
    return x
