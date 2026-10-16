"""Reading a health paper (vision model, always at High: the caller's privacy scope carries the level).

Returns what the paper says, structured: whose it is, when, who issued it, what kind of event, which areas
and conditions it touches, a short summary, amounts, an appointment if there is one, and EVERY measurement
as a number with its unit and reference range. Nothing here decides what leaves the Hub."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Sequence

from app.domains.health.categories import RECORD_KINDS
from app.llm_gateway import VISION_EXTRACTION, get_gateway
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


@dataclass
class ExtractedHealth:
    person_name: Optional[str] = None
    document_date: Optional[date] = None
    provider: Optional[str] = None
    provider_phone: Optional[str] = None
    provider_address: Optional[str] = None
    record_kind: Optional[str] = None
    areas: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    summary: str = ""
    amount_billed: Optional[float] = None
    amount_reimbursed: Optional[float] = None
    amount_owed: Optional[float] = None
    appointment_date: Optional[date] = None
    appointment_time: Optional[str] = None
    measurements: list[ExtractedMeasurement] = field(default_factory=list)
    full_text: str = ""


_KINDS = ", ".join(v for v, _ in RECORD_KINDS)

_SYSTEM = f"""You read a family's medical paper (Portuguese or English) and return what it says as JSON.
Be faithful: never invent a value, a date or a name. Use null when the paper does not say.

Fields:
- person_name: the patient the paper is about (full name as printed).
- document_date: the date printed on the paper, YYYY-MM-DD.
- provider: the doctor, clinic, lab, hospital, pharmacy or insurer that issued it; provider_phone, provider_address if printed.
- record_kind: one of {_KINDS}.
- areas: body areas/systems it concerns (e.g. Eyes, Skin, Heart and circulation, Digestive system, Teeth).
- conditions: named conditions or diagnoses it concerns (e.g. Stroke, Diabetes). [] if none.
- summary: two sentences, plain words, what this paper is and its main finding.
- amount_billed, amount_reimbursed, amount_owed: euros as numbers, for bills and reimbursement statements.
- appointment_date (YYYY-MM-DD) and appointment_time (HH:MM, 24h): only for an appointment confirmation.
- measurements: EVERY measured value (test, value as a number, unit, reference range low/high, date measured).
  If a value is unclear or you are unsure of it, set uncertain=true. source_text is the line as printed.
- full_text: the paper's text, transcribed.

Respond with ONLY the JSON object."""

_NUM = {"type": ["number", "null"]}
_STR = {"type": ["string", "null"]}
_SCHEMA = {
    "type": "object",
    "required": ["summary"],
    "properties": {
        "person_name": _STR, "document_date": _STR, "provider": _STR, "provider_phone": _STR,
        "provider_address": _STR, "record_kind": _STR,
        "areas": {"type": "array", "items": {"type": "string"}},
        "conditions": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "amount_billed": _NUM, "amount_reimbursed": _NUM, "amount_owed": _NUM,
        "appointment_date": _STR, "appointment_time": _STR,
        "measurements": {"type": "array", "items": {"type": "object", "required": ["test"], "properties": {
            "test": {"type": "string"}, "value": _NUM, "value_text": _STR, "unit": _STR, "ref_low": _NUM,
            "ref_high": _NUM, "measured_on": _STR, "uncertain": {"type": ["boolean", "null"]},
            "source_text": _STR}}},
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
    text = str(value).strip() if value is not None else ""
    return text or None


def _names(values) -> list[str]:
    return [t for t in (str(v).strip() for v in (values or [])) if t]


async def extract_health_document(
    file_path: str, category: Optional[str], *, existing_areas: Sequence[str] = (),
    existing_conditions: Sequence[str] = (), gateway=None,
) -> ExtractedHealth:
    if not is_model_readable(file_path):
        raise HealthExtractionError("This file type can't be read automatically (only PDFs and photos).")
    system = _SYSTEM
    if existing_areas:
        system += "\n\nExisting areas (reuse a name exactly when it fits): " + "; ".join(existing_areas)
    if existing_conditions:
        system += "\nExisting conditions (reuse a name exactly when it fits): " + "; ".join(existing_conditions)
    gw = gateway or get_gateway()
    result = await gw.run(
        VISION_EXTRACTION, system, f"Paper type: {category or 'unknown'}. Read it as JSON.",
        attachments=[build_content_block(file_path)], response_schema=_SCHEMA, max_tokens=12000,
    )
    try:
        data = json.loads(strip_json_fences(result.text))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        kinds = {v for v, _ in RECORD_KINDS}
        measurements = [
            ExtractedMeasurement(
                test=str(m["test"]).strip(), value=_num(m.get("value")), value_text=_text(m.get("value_text")),
                unit=_text(m.get("unit")), ref_low=_num(m.get("ref_low")), ref_high=_num(m.get("ref_high")),
                measured_on=_date(m.get("measured_on")), uncertain=bool(m.get("uncertain")),
                source_text=_text(m.get("source_text")) or "")
            for m in data.get("measurements") or [] if str(m.get("test") or "").strip()
        ]
        kind = _text(data.get("record_kind"))
        return ExtractedHealth(
            person_name=_text(data.get("person_name")), document_date=_date(data.get("document_date")),
            provider=_text(data.get("provider")), provider_phone=_text(data.get("provider_phone")),
            provider_address=_text(data.get("provider_address")), record_kind=kind if kind in kinds else None,
            areas=_names(data.get("areas")), conditions=_names(data.get("conditions")),
            summary=_text(data.get("summary")) or "", amount_billed=_num(data.get("amount_billed")),
            amount_reimbursed=_num(data.get("amount_reimbursed")), amount_owed=_num(data.get("amount_owed")),
            appointment_date=_date(data.get("appointment_date")), appointment_time=_text(data.get("appointment_time")),
            measurements=measurements, full_text=_text(data.get("full_text")) or "")
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HealthExtractionError(f"Could not parse the reading of the paper: {exc}") from exc
