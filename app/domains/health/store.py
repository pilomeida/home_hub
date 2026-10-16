"""Writing what was read from a health paper into Health's tables, and taking it back out."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

from sqlmodel import Session, select

from app.domains.fields import dump_fields, load_fields
from app.domains.health import areas as areas_svc
from app.domains.health import people as people_svc
from app.domains.health.extraction import ExtractedHealth, ExtractedMeasurement
from app.domains.health.text import match_key
from app.models.document import Document
from app.models.health import HealthEntry, HealthEntryTag, HealthMeasurement


def parse_amount(text) -> Optional[float]:
    """'1.234,56' -> 1234.56; '45,5 €' -> 45.5; '1234.56' -> 1234.56; anything else -> None."""
    if text is None:
        return None
    raw = re.sub(r"[^\d,.\-]", "", str(text))
    if not re.search(r"\d", raw):
        return None
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def measurement_needs_check(m: ExtractedMeasurement) -> bool:
    """Doubtful: the reader was unsure, there is no result at all, or a number has neither a unit nor a reference
    range (a bare number could be anything; pH, a density or a ratio carry a range). A limit ("<3") or a non-numeric
    result ("não detectável") is a result, not a doubt."""
    no_result = m.value is None and not (m.value_text or "").strip()
    bare_number = m.value is not None and not (m.unit or "").strip() and m.ref_low is None and m.ref_high is None
    return bool(m.uncertain or no_result or bare_number)


def _out_of_range(m: ExtractedMeasurement) -> Optional[bool]:
    if m.value is None or (m.ref_low is None and m.ref_high is None):
        return None
    return bool((m.ref_low is not None and m.value < m.ref_low) or (m.ref_high is not None and m.value > m.ref_high))


def entry_for(session: Session, document_id: int) -> Optional[HealthEntry]:
    return session.exec(select(HealthEntry).where(HealthEntry.document_id == document_id)).first()


def _fill(fields: dict[str, str], key: str, value) -> None:
    if value not in (None, "") and not fields.get(key):
        fields[key] = value.isoformat() if isinstance(value, date) else str(value)


def sync_entry_from_fields(session: Session, document: Document, *, full_text: Optional[str] = None) -> HealthEntry:
    """(Re)build the entry's columns from the document's fields, the single source of what a person sees and edits."""
    fields = load_fields(document)
    entry = entry_for(session, document.id) or HealthEntry(document_id=document.id)
    person = people_svc.find_or_create_person(session, fields.get("person"))
    entry.person_id = person.id if person else None
    try:
        entry.entry_date = date.fromisoformat(fields["document_date"]) if fields.get("document_date") else None
    except ValueError:
        entry.entry_date = None
    entry.year = entry.entry_date.year if entry.entry_date else None
    entry.kind = fields.get("record_kind") or None
    entry.provider = fields.get("provider") or None
    entry.summary = fields.get("summary") or None
    entry.amount_billed = parse_amount(fields.get("amount_billed"))
    entry.amount_reimbursed = parse_amount(fields.get("amount_reimbursed"))
    entry.amount_owed = parse_amount(fields.get("amount_owed"))
    if full_text is not None:
        entry.full_text = full_text
    entry.updated_at = datetime.utcnow()
    session.add(entry)
    session.commit()
    session.refresh(entry)
    for m in session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)).all():
        m.person_id = entry.person_id
        session.add(m)
    entry.needs_check = bool(entry.person_id is None or entry.entry_date is None or any(
        m.needs_check for m in session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id))))
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def apply_extraction(session: Session, document: Document, extracted: ExtractedHealth) -> HealthEntry:
    fields = load_fields(document)               # what a person typed stays; the reading only fills blanks
    _fill(fields, "person", extracted.person_name)
    _fill(fields, "document_date", extracted.document_date)
    _fill(fields, "provider", extracted.provider)
    _fill(fields, "provider_phone", extracted.provider_phone)
    _fill(fields, "provider_address", extracted.provider_address)
    _fill(fields, "record_kind", extracted.record_kind)
    _fill(fields, "summary", extracted.summary)
    _fill(fields, "details", extracted.details)
    _fill(fields, "appointment_date", extracted.appointment_date)
    _fill(fields, "appointment_time", extracted.appointment_time)
    for key, value in (("amount_billed", extracted.amount_billed), ("amount_reimbursed", extracted.amount_reimbursed),
                       ("amount_owed", extracted.amount_owed)):
        _fill(fields, key, value)
    document.fields_json = dump_fields(fields)
    session.add(document)
    session.commit()

    entry = sync_entry_from_fields(session, document, full_text=extracted.full_text)
    for old in session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)).all():
        session.delete(old)
    for m in extracted.measurements:
        session.add(HealthMeasurement(
            entry_id=entry.id, person_id=entry.person_id, measured_on=m.measured_on or entry.entry_date,
            test=m.test, test_key=match_key(m.test), value=m.value, value_text=m.value_text, unit=m.unit,
            ref_low=m.ref_low, ref_high=m.ref_high, out_of_range=_out_of_range(m),
            needs_check=measurement_needs_check(m), source_text=m.source_text or None))
    session.commit()
    areas_svc.set_tags(session, entry, extracted.areas, extracted.conditions)
    return sync_entry_from_fields(session, document)


def wiki_context(extracted: ExtractedHealth) -> str:
    lines = [f"Person: {extracted.person_name or 'unknown'}",
             f"Date: {extracted.document_date or 'unknown'}",
             f"Provider: {extracted.provider or 'unknown'}",
             f"Areas: {', '.join(extracted.areas) or '-'}; conditions: {', '.join(extracted.conditions) or '-'}",
             f"Summary: {extracted.summary}"]
    for m in extracted.measurements:
        value = m.value if m.value is not None else (m.value_text or "?")
        lines.append(f"- {m.test}: {value} {m.unit or ''} (reference {m.ref_low}–{m.ref_high})")
    return "\n".join(lines)


def purge_derived(session: Session, document: Document) -> None:
    """Take back everything read from this paper: measurements, tags, the entry. (The wiki claims are
    withdrawn by the ingestion core; the calendar entry by Task 11.)"""
    entry = entry_for(session, document.id)
    if entry is None:
        return
    for m in session.exec(select(HealthMeasurement).where(HealthMeasurement.entry_id == entry.id)).all():
        session.delete(m)
    for t in session.exec(select(HealthEntryTag).where(HealthEntryTag.entry_id == entry.id)).all():
        session.delete(t)
    session.delete(entry)
    session.commit()


async def discard_document(session: Session, document: Document, calendar=None) -> Document:
    """Take a health paper out of use: its calendar entry, wiki claims, measurements, tags and entry are
    withdrawn; the row and file are kept (raw sources are immutable) and the paper is marked discarded."""
    from app.domains.health.appointments import remove_appointment
    from app.models.document import DocumentStatus
    from app.services.wiki_engine import withdraw_from_wiki

    await remove_appointment(session, document, calendar=calendar)
    await withdraw_from_wiki(session, document, description=f"{document.filename} deleted from Health")
    purge_derived(session, document)
    document.status = DocumentStatus.DISCARDED
    session.add(document)
    session.commit()
    session.refresh(document)
    return document
