"""Reading a health paper (always at High: the caller's privacy scope carries the level). A PDF that carries its own
text goes to the model as that text; a scan or photo goes as page images.

Returns what the paper says, structured: whose it is, when, who issued it, what kind of event, which areas
and conditions it touches, a short summary, amounts, an appointment if there is one, and EVERY result,
grouped under the headings the paper prints it under, with its unit and the reference printed beside it. Nothing here decides what leaves the Hub."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Sequence

from app.domains.health.categories import ALL_CATEGORIES, RECORD_KINDS
from app.domains.health.text import PATH_SEP, limits_from_reference, spaced_intervals, split_method
from app.llm_gateway import HEALTH_EXTRACTION, get_gateway
from app.privacy.tiers import Tier
from app.services.document_input import build_content_block, is_model_readable
from app.services.json_utils import strip_json_fences


class HealthExtractionError(Exception):
    pass


@dataclass
class ExtractedMeasurement:
    test: str
    value: Optional[float]
    value_text: Optional[str]
    unit: Optional[str]
    ref_low: Optional[float]
    ref_high: Optional[float]
    measured_on: Optional[date]
    uncertain: bool
    source_text: str
    group_path: str = ""            # the headings above it on the paper, outermost first, joined with " › "
    position: int = 0               # order on the paper (an earlier result shares its test's position)
    reference_text: Optional[str] = None    # everything printed as reference beside it, as printed
    method: Optional[str] = None            # the method the lab names, kept apart from the test's name
    is_previous: bool = False       # an earlier result of the same test, printed on this paper for comparison


@dataclass
class ExtractedHealth:
    category: Optional[str] = None
    person_name: Optional[str] = None
    document_date: Optional[date] = None
    provider: Optional[str] = None
    provider_phone: Optional[str] = None
    provider_address: Optional[str] = None
    record_kind: Optional[str] = None
    areas: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    summary: str = ""
    details: str = ""
    indication: str = ""            # why the paper was made, as printed ("Indicação")
    clinical_notes: str = ""        # what the requester wrote about the patient ("Observações")
    requested_by: str = ""          # the service or doctor who asked for it / sent the patient
    performed_by: str = ""          # who did the exam or signed the report
    check_notes: list[str] = field(default_factory=list)    # what the check against the paper could not settle (reading.py)
    amount_billed: Optional[float] = None
    amount_reimbursed: Optional[float] = None
    amount_owed: Optional[float] = None
    appointment_date: Optional[date] = None
    appointment_time: Optional[str] = None
    measurements: list[ExtractedMeasurement] = field(default_factory=list)
    full_text: str = ""
    layout: str = ""               # "sheet" (a table of results) | "report" (written text under headings) | "" (not said)


_KINDS = ", ".join(v for v, _ in RECORD_KINDS)
_CATEGORIES = ", ".join(sorted(ALL_CATEGORIES))

_SYSTEM = f"""You read a family's medical paper (Portuguese or English) and return what it says as JSON.
Be faithful: never invent a value, a date or a name. Leave out any key the paper does not support.

Fields:
- category: what kind of paper this is, one of {_CATEGORIES}; null if it is not a medical paper or you cannot tell.
- person_name: the patient the paper is about (full name as printed).
- document_date: the date printed on the paper, YYYY-MM-DD.
- provider: the doctor, clinic, lab, hospital, pharmacy or insurer that issued it, named once as printed; provider_phone, provider_address if printed.
- record_kind: one of {_KINDS}.
- areas: every body area/system it concerns, as many as apply (a lab panel usually touches several) (e.g. Eyes, Skin, Heart and circulation, Digestive system, Teeth).
- conditions: named conditions or diagnoses it concerns (e.g. Stroke, Diabetes). [] if none.
  For areas and conditions, reuse an existing name exactly when it fits; write a new name in the language of the paper.
- layout: "sheet" if the paper is mostly a table of results (a laboratory sheet), "report" if it is mostly written text under headings (an imaging, consultation or examination report).
- summary: two sentences, plain words, what this paper is and its main finding. Write it in the language of the paper.
- indication: why the paper was requested or made, in the paper's own words (e.g. the line labelled "Indicação" or "Motivo");
  leave the key out if the paper gives none.
- clinical_notes: what the requester wrote about the patient (e.g. "Observações", "Informação clínica": earlier diagnoses,
  the clinical question), word for word; leave it out if none.
- requested_by: the service or doctor who asked for it or sent the patient (e.g. "Proveniência", "Médico requisitante"), as printed.
- performed_by: who did the exam or signed the report (technician, specialist), as printed.
- details: ONE string with EVERY other item printed on the paper that has no field here, one per line (separated by \\n) as "Label: value", using the
  paper's own labels and language: the patient's identification, tax, health-service and beneficiary numbers, age,
  the dates of collection, entry and release, requisition and internal numbers, the laboratory's director, specialists and
  technicians, accreditations, methods, and any printed note or comment that is not a section's conclusion, impression or comment (those are results, see sections). Do not repeat the four items above here.
- amount_billed, amount_reimbursed, amount_owed: euros as numbers, for bills and reimbursement statements.
- appointment_date (YYYY-MM-DD) and appointment_time (HH:MM, 24h): only for an appointment confirmation.
- sections: EVERY result printed on the paper, numeric or not, none left out, grouped exactly as the paper groups them.
  A section is {{"path": [...], "results": [...]}}. path = the headings the results are printed under, outermost
  first (e.g. ["Estudo da urina", "Urina tipo II", "Caracteres gerais"] for pH and Densidade); a result with no
  heading goes in a section with path []. Keep the paper's order. A line that has results indented beneath it is a
  heading and belongs in path, even if it is a test's own name: "Resultado (NGSP/DCCT)" and "Resultado (IFCC)" sit
  under the heading "Hemoglobina glicada A1c", which sits under "Bioquímica"; "Doseamento" sits under "Proteína C reativa".
  Results continue under the same heading until the paper shows another heading.
  The headings are an outline. A paper can have main sections (e.g. "Triplex carotídeo", "ECO/DTC", "DTC-c") and,
  inside a main section, chapters (e.g. "Eixo Carotídeo direito", "Eixo Carotídeo esquerdo" and "Artérias Vertebrais"
  inside "Triplex carotídeo"). Put the whole chain in path, outermost first: ["Triplex carotídeo", "Eixo Carotídeo direito"].
  Never put a heading's name inside a test's name, and never gather conclusions under one invented heading such as
  "Conclusões": each conclusion belongs under the section or chapter it describes.
  Printed prose is a result too: a conclusion, impression or comment under a section is a result with test "Conclusão"
  (or the paper's own word: "Impressão", "Comentário") and the whole text, word for word, in value_text.
  Every number printed in a section (velocities, indices, diameters, percentages) is a separate result in that same
  section, with its unit.
  In a written report the headings ARE the topics: keep the report's own outline exactly as printed, give each topic's written conclusion as the result "Conclusão" under it, and make every number the report states (in text or in a table) a separate result under the topic it belongs to, with its unit. A report that prints no numbers has none: never invent a measurement.
  Keep a method note such as "(Mét.CF/COL)" inside the heading or test name exactly as printed.
  Write every heading and test name in normal capitalisation, not in capitals: "Hematologia" not "HEMATOLOGIA",
  "Creatinina" not "CREATININA", "Gama glutamil transpeptidase" not "GAMA GLUTAMIL TRANSPEPTIDASE"; acronyms stay
  as they are (AST, ALT, HDL, VGM, PSA, TSH).
  A result is {{"test", "value", "value_text", "unit", "reference", "measured_on", "previous", "uncertain"}}:
  * test: its name as printed, without the headings (those belong in path).
  * a number: value as a number, unit as printed.
  * a limit such as "< 3" or a non-numeric result such as "não detectável": value_text exactly as printed, no value.
  * reference: everything the paper prints beside THAT result as its reference, as one string: the interval, a
    one-sided limit, and risk levels, e.g. "13,0 - 17,0", "< 20", "Risco elevado: < 40; Risco baixo: > 60".
    Write every interval as "a - b" with a space either side of the dash. A reference belongs to the result in its
    own unit. A line can print two results (a percentage and an absolute count, e.g. Neutrófilos 76,5 % and
    5,42 x10^9/L): return BOTH, as two results; a reference printed once belongs to the one in its unit.
  * measured_on (YYYY-MM-DD): only when it differs from the paper's date.
  * previous: earlier results the paper prints for the same test, for comparison: [{{"date": "YYYY-MM-DD", "value": n}}]
    or [{{"date": "YYYY-MM-DD", "value_text": "..."}}]. However the paper lays them out (a history column headed by
    an earlier date such as 10-05-2021, several dated columns, a trend table, a note such as "last year: 5,2"),
    every result with a date other than the paper's is a previous result with that date, not a reference: that
    includes words such as "não detectável".
  * uncertain: true if a value is unclear or you are unsure of it.
  Omit every key that does not apply (no nulls). Answer in compact JSON: no indentation, no line breaks inside it.
{{full_text_rule}}
Respond with ONLY the JSON object."""

_TEXT_AND_PAGES_RULE = (
    "The paper is given twice: as page images, which show its visual structure (grey bands, headings, indentation,\n"
    "which values sit in which column), and as its exact text. Take every number and word from the TEXT; take the\n"
    "headings and how the results are grouped from the IMAGES.\n")
_FULL_TEXT_RULE = '- full_text: the paper\'s text, transcribed.\n'
_TEXT_GIVEN_RULE = "The paper's text is given to you, so do not return full_text.\n"

_NUM = {"type": ["number", "null"]}
_STR = {"type": ["string", "null"]}
_PREVIOUS = {"type": "array", "items": {"type": "object", "properties": {
    "date": _STR, "value": _NUM, "value_text": _STR}}}
_SCHEMA = {
    "type": "object",
    "required": ["summary"],
    "properties": {
        "category": _STR, "layout": _STR, "person_name": _STR, "document_date": _STR, "provider": _STR, "provider_phone": _STR,
        "provider_address": _STR, "record_kind": _STR,
        "indication": _STR, "clinical_notes": _STR, "requested_by": _STR, "performed_by": _STR,
        "details": {"type": ["string", "array", "null"], "items": {"type": "string"}},       # one string; a list of lines is accepted too
        "areas": {"type": "array", "items": {"type": "string"}},
        "conditions": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "amount_billed": _NUM, "amount_reimbursed": _NUM, "amount_owed": _NUM,
        "appointment_date": _STR, "appointment_time": _STR,
        "sections": {"type": "array", "items": {"type": "object", "required": ["results"], "properties": {
            "path": {"type": "array", "items": {"type": "string"}},
            "results": {"type": "array", "items": {"type": "object", "required": ["test"], "properties": {
                "test": {"type": "string"}, "value": _NUM, "value_text": _STR, "unit": _STR, "reference": _STR,
                "measured_on": _STR, "previous": _PREVIOUS,
                "uncertain": {"type": ["boolean", "null"]}}}}}}},
        "full_text": _STR,
    },
}


def _date(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _num(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value) -> Optional[str]:
    if isinstance(value, (list, tuple)):          # a list of lines where one string was asked for: join, never drop
        value = "\n".join(str(v).strip() for v in value if str(v).strip())
    text = str(value).strip() if value is not None else ""
    return text or None


def _names(values) -> list[str]:
    return [t for t in (str(v).strip() for v in (values or [])) if t]


def _measurements(data: dict) -> list[ExtractedMeasurement]:
    """Flatten the sections into rows: each result in the paper's order, its earlier results right after it."""
    rows: list[ExtractedMeasurement] = []
    position = 0

    def add(item, path: str, heading_method: Optional[str] = None) -> None:
        nonlocal position
        test, own_method = split_method(str(item.get("test") or ""))
        if not test:
            return
        leaf = path.rsplit(PATH_SEP, 1)[-1]
        if leaf and leaf.casefold() == test.casefold():                      # "Doseamento" under "Doseamento"
            path = path.rsplit(PATH_SEP, 1)[0] if PATH_SEP in path else ""
        elif leaf:                                                           # "Hemoglobina glicada A1c - Resultado (IFCC)" under that heading
            prefixed = re.match(rf"^{re.escape(leaf)}\s*[-–—:]\s*(.+)$", test, re.IGNORECASE)
            if prefixed:
                test = prefixed.group(1).strip()
        position += 1
        ref_text = spaced_intervals(_text(item.get("reference") or item.get("reference_text")))
        low, high = limits_from_reference(ref_text)
        low = _num(item.get("ref_low")) if _num(item.get("ref_low")) is not None else low      # a stated number wins
        high = _num(item.get("ref_high")) if _num(item.get("ref_high")) is not None else high
        unit = _text(item.get("unit"))
        rows.append(ExtractedMeasurement(
            test=test, value=_num(item.get("value")), value_text=_text(item.get("value_text")), unit=unit,
            ref_low=low, ref_high=high, measured_on=_date(item.get("measured_on")),
            uncertain=bool(item.get("uncertain")), source_text=_text(item.get("source_text")) or "",
            group_path=path, position=position, reference_text=ref_text, method=own_method or heading_method))
        for earlier in item.get("previous") or []:
            if not isinstance(earlier, dict):
                continue
            rows.append(ExtractedMeasurement(
                test=test, value=_num(earlier.get("value")), value_text=_text(earlier.get("value_text")), unit=unit,
                ref_low=None, ref_high=None, measured_on=_date(earlier.get("date")), uncertain=False, source_text="",
                group_path=path, position=position, reference_text=None, method=own_method or heading_method,
                is_previous=True))

    for section in data.get("sections") or []:
        headings, methods = [], []
        for raw in section.get("path") or []:
            name, method = split_method(str(raw))
            if name:
                headings.append(name)
            if method:
                methods.append(method)
        for item in section.get("results") or []:
            add(item, PATH_SEP.join(headings), methods[-1] if methods else None)
    for item in data.get("measurements") or []:      # an older, flat reading: no headings
        add(item, "")
    return rows


def paper_text(file_path: str) -> Optional[str]:
    """The text a PDF carries itself (layout kept), or None when it has none worth using (a scan, a photo):
    then the page images go to the reader instead. Taking the text from the file is instant and exact."""
    if not file_path.lower().endswith(".pdf"):
        return None
    text: Optional[str] = None
    exe = shutil.which("pdftotext")
    if exe is not None:
        try:
            done = subprocess.run([exe, "-layout", file_path, "-"], capture_output=True, timeout=30, check=False)
            if done.returncode == 0:
                text = done.stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            text = None
    if text is None:
        try:
            from pypdf import PdfReader
            text = "\f".join(page.extract_text(extraction_mode="layout") or "" for page in PdfReader(file_path).pages)
        except Exception:
            return None
    pages = max(text.count("\f"), 1)
    cleaned = re.sub(r"[ \t]{3,}", "  ", text.replace("\f", "\n\n"))
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned if len(re.sub(r"\s", "", cleaned)) >= 200 * pages else None


async def extract_health_document(
    file_path: str, category: Optional[str], *, existing_areas: Sequence[str] = (),
    existing_conditions: Sequence[str] = (), gateway=None, guidance: str = "",
) -> ExtractedHealth:
    if not is_model_readable(file_path):
        raise HealthExtractionError("This file type can't be read automatically (only PDFs and photos).")
    text = await asyncio.to_thread(paper_text, file_path)
    system = _SYSTEM.replace("{full_text_rule}", (_TEXT_AND_PAGES_RULE + _TEXT_GIVEN_RULE) if text else _FULL_TEXT_RULE)
    if existing_areas:
        system += "\n\nExisting areas (reuse a name exactly when it fits): " + "; ".join(existing_areas)
    if existing_conditions:
        system += "\nExisting conditions (reuse a name exactly when it fits): " + "; ".join(existing_conditions)
    if guidance:
        system += "\n\n" + guidance
    gw = gateway or get_gateway()
    user = f"Paper type: {category or 'not known yet: work it out'}. Read it as JSON."
    if text:
        user += "\n\nThe paper's exact text, taken from its PDF (the order of lines may be scrambled):\n\n" + text
    # Always High, stated here and not left to the caller's privacy scope: a health paper must never be routable
    # anywhere else, however this reader is reached (an explicit sensitivity always wins over the scope).
    result = await gw.run(HEALTH_EXTRACTION, system, user, sensitivity=Tier.HIGH.value,
                          attachments=[build_content_block(file_path)], response_schema=_SCHEMA)
    try:
        data = json.loads(strip_json_fences(result.text))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        kinds = {v for v, _ in RECORD_KINDS}
        kind = _text(data.get("record_kind"))
        layout = _text(data.get("layout"))
        category_read = _text(data.get("category"))
        return ExtractedHealth(
            category=category_read if category_read in ALL_CATEGORIES else None,
            person_name=_text(data.get("person_name")), document_date=_date(data.get("document_date")),
            provider=_text(data.get("provider")), provider_phone=_text(data.get("provider_phone")),
            provider_address=_text(data.get("provider_address")), record_kind=kind if kind in kinds else None,
            areas=_names(data.get("areas")), conditions=_names(data.get("conditions")),
            summary=_text(data.get("summary")) or "", details=_text(data.get("details")) or "",
            indication=_text(data.get("indication")) or "", clinical_notes=_text(data.get("clinical_notes")) or "",
            requested_by=_text(data.get("requested_by")) or "", performed_by=_text(data.get("performed_by")) or "",
            amount_billed=_num(data.get("amount_billed")), amount_reimbursed=_num(data.get("amount_reimbursed")),
            amount_owed=_num(data.get("amount_owed")),
            appointment_date=_date(data.get("appointment_date")), appointment_time=_text(data.get("appointment_time")),
            layout=layout if layout in ("sheet", "report") else "", measurements=_measurements(data), full_text=text or _text(data.get("full_text")) or "")
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HealthExtractionError(f"Could not parse the reading of the paper: {exc}") from exc
