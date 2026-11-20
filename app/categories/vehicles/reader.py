"""The Vehicles reader (PLAN step 8c): the plate, the car, and the dates each kind of paper carries.

What a person typed is kept; the reader fills only empty details. Dates as printed (dd/mm/aaaa on Portuguese
papers), returned as ISO."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.llm_gateway import VISION_EXTRACTION, get_gateway
from app.services.document_input import build_content_block
from app.services.reading import ReadingError, cut_text, day, json_object

_MAX_TEXT = 20_000

_SYSTEM = """You read one paper about a household's car: its registration (Documento Unico Automovel, livrete), an
inspection certificate (IPO, ficha de inspecao), a road tax (IUC) notice or proof of payment, or a workshop's service
invoice or record. Return ONLY a JSON object:
{"plate": "...", "vehicle": "...", "paper_date": "YYYY-MM-DD or null", "next_inspection_on": "YYYY-MM-DD or null",
 "road_tax_due_on": "YYYY-MM-DD or null", "next_service_on": "YYYY-MM-DD or null", "mileage": "..."}
- plate: the registration plate (matricula) as printed, e.g. "82-CL-65".
- vehicle: make and model, e.g. "Toyota Avensis"; "" if not printed.
- paper_date: the date the paper was issued, the inspection was done, the tax was paid or the service was done.
- next_inspection_on: on an inspection paper, the date of the next inspection ("proxima inspecao", "valida ate").
- road_tax_due_on: on a road tax paper, the payment deadline printed ("data limite de pagamento"); null if none.
- next_service_on: on a service paper, the next service date if printed.
- mileage: the odometer reading in km as printed, digits only; "" if none.
Use null or "" for anything not printed. Never guess a date."""

_SCHEMA = {"type": "object", "properties": {k: {"type": ["string", "null"]} for k in (
    "plate", "vehicle", "paper_date", "next_inspection_on", "road_tax_due_on", "next_service_on", "mileage")}}


class VehicleReadingError(ReadingError):
    pass


@dataclass(frozen=True)
class VehicleReading:
    plate: str
    vehicle: str
    paper_date: Optional[date]
    next_inspection_on: Optional[date]
    road_tax_due_on: Optional[date]
    next_service_on: Optional[date]
    mileage: str


async def read_vehicle_paper(file_path: str, text: Optional[str], *, gateway=None) -> VehicleReading:
    gw = gateway or get_gateway()
    if text:
        body, _cut = cut_text(text, _MAX_TEXT)
        user, attachments = "The paper's text, taken from its PDF:\n\n" + body + "\n\nRead it as JSON.", None
    else:
        user, attachments = "Read this paper as JSON.", [await asyncio.to_thread(build_content_block, file_path)]
    result = await gw.run(VISION_EXTRACTION, _SYSTEM, user, attachments=attachments, response_schema=_SCHEMA)
    data = json_object(result.text, error=VehicleReadingError)
    text_of = lambda k, n: " ".join(str(data.get(k) or "").split())[:n]  # noqa: E731
    return VehicleReading(text_of("plate", 20).upper(), text_of("vehicle", 80), day(data.get("paper_date")),
                          day(data.get("next_inspection_on")), day(data.get("road_tax_due_on")),
                          day(data.get("next_service_on")), "".join(ch for ch in text_of("mileage", 20) if ch.isdigit()))
