"""Vehicles handler (PLAN step 8c): read the paper (plate, car, dates), keep what a person typed, write the plate one
way and put the car in the subjects registry. A road-tax date already past is the year's paid one: the next is a year
on. The kind's date reminder (app/categories/reminders.py) is kept in step by filing and editing."""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

from sqlmodel import Session

from app.categories.base import CategoryHandler
from app.categories.fields import dump_fields, field_date, load_fields
from app.categories.vehicles.kinds import VehicleKind
from app.models.document import Document, DocumentStatus
from app.services.document_input import is_model_readable
from app.services.loan_math import add_months

READ_FIELDS = ("plate", "vehicle", "paper_date", "next_inspection_on", "road_tax_due_on", "next_service_on", "mileage")
DATE_OF = {VehicleKind.INSPECTION.value: ("next_inspection_on", "the next inspection date"),
           VehicleKind.ROAD_TAX.value: ("road_tax_due_on", "the next payment date"),
           VehicleKind.SERVICE.value: ("next_service_on", "the next service date")}


def plate_key(plate: str) -> str:
    """"82-cl-65", "82 CL 65" and "82CL65" are one plate: "82-CL-65" (pairs joined by dashes when it has six)."""
    bare = re.sub(r"[^0-9A-Za-z]", "", plate or "").upper()
    return "-".join(bare[i:i + 2] for i in range(0, 6, 2)) if len(bare) == 6 else bare


def _next_year_if_past(value: str, today: date) -> str:
    when = field_date({"d": value}, "d")
    while when is not None and when < today:
        when = add_months(when, 12)
    return when.isoformat() if when else value


def _settle(session: Session, fields: dict) -> dict:
    from app.models.subject import SubjectKind
    from app.services import subjects
    if fields.get("plate"):
        fields["plate"] = plate_key(fields["plate"])
        subjects.register(session, SubjectKind.THING, fields["plate"])
    return fields


def _note(document: Document, fields: dict) -> Optional[str]:
    key, words = DATE_OF.get(document.paper_kind or "", (None, ""))
    if key and not fields.get(key):
        return f"{words[0].upper()}{words[1:]} was not found on the paper. Type it in the details below and save."
    return None


class VehiclesHandler(CategoryHandler):
    async def process(self, session: Session, document: Document) -> Document:
        from app.categories.vehicles import reader
        from app.services.paper_search import store_pdf_text
        fields = load_fields(document)
        if any(not fields.get(k) for k in READ_FIELDS) and is_model_readable(document.file_path):
            row = store_pdf_text(session, document)
            r = await reader.read_vehicle_paper(document.file_path, row.text if row is not None else None)
            iso = lambda d: d.isoformat() if d else ""  # noqa: E731
            found = {"plate": r.plate, "vehicle": r.vehicle, "paper_date": iso(r.paper_date),
                     "next_inspection_on": iso(r.next_inspection_on), "next_service_on": iso(r.next_service_on),
                     "road_tax_due_on": _next_year_if_past(iso(r.road_tax_due_on), date.today()), "mileage": r.mileage}
            fields = {**{k: v for k, v in found.items() if v}, **{k: v for k, v in fields.items() if v}}
        document.fields_json = dump_fields(_settle(session, fields))
        document.status = DocumentStatus.PROCESSED
        document.failure_reason = _note(document, fields)
        session.add(document)
        session.commit()
        session.refresh(document)
        return document

    async def on_fields_changed(self, session: Session, document: Document, previous_fields: dict[str, str]) -> None:
        fields = _settle(session, load_fields(document))
        document.fields_json = dump_fields(fields)
        document.failure_reason = _note(document, fields)
        session.add(document)
        session.commit()
