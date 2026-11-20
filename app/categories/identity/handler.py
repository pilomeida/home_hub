"""Identity & Legal handler (PLAN step 8a): read the paper (holder, number, dates, country), keep what a person
typed, and put the holder in the subjects registry under the name it keeps. The renewal To-Do is the kind's date
reminder (app/categories/reminders.py), kept in step by filing and editing."""

from __future__ import annotations

from sqlmodel import Session

from app.categories.base import CategoryHandler
from app.categories.fields import dump_fields, load_fields
from app.categories.identity.kinds import EXPIRING
from app.models.document import Document, DocumentStatus
from app.services.document_input import is_model_readable

READ_FIELDS = ("holder", "document_number", "issued_on", "expires_on", "country")
MISSING_EXPIRY = ("Its expiry date was not found on the paper, so no renewal reminder was made. Type the expiry "
                  "date in the details below and save.")


def _settle_holder(session: Session, fields: dict) -> dict:
    """The holder is a person of the registry: a spelling it already knows is written as the name it keeps."""
    from app.services import subjects
    holder = (fields.get("holder") or "").strip()
    subject = subjects.register_person(session, holder) if holder else None
    if subject is not None:
        fields["holder"] = subject.name
    return fields


def _note(document: Document, fields: dict):
    return MISSING_EXPIRY if document.paper_kind in EXPIRING and not fields.get("expires_on") else None


class IdentityHandler(CategoryHandler):
    async def process(self, session: Session, document: Document) -> Document:
        from app.categories.identity import reader
        from app.services.paper_search import store_pdf_text
        fields = load_fields(document)
        if any(not fields.get(k) for k in READ_FIELDS) and is_model_readable(document.file_path):
            row = store_pdf_text(session, document)
            reading = await reader.read_identity_paper(document.file_path, row.text if row is not None else None)
            found = {"holder": reading.holder, "document_number": reading.number, "country": reading.country,
                     "issued_on": reading.issued_on.isoformat() if reading.issued_on else "",
                     "expires_on": reading.expires_on.isoformat() if reading.expires_on else ""}
            fields = {**{k: v for k, v in found.items() if v}, **{k: v for k, v in fields.items() if v}}
        document.fields_json = dump_fields(_settle_holder(session, fields))
        document.status = DocumentStatus.PROCESSED
        document.failure_reason = _note(document, fields)
        session.add(document)
        session.commit()
        session.refresh(document)
        return document

    async def on_fields_changed(self, session: Session, document: Document, previous_fields: dict[str, str]) -> None:
        fields = _settle_holder(session, load_fields(document))
        document.fields_json = dump_fields(fields)
        document.failure_reason = _note(document, fields)
        session.add(document)
        session.commit()
