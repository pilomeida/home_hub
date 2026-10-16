"""Health domain handler: read the paper, store what it says, put the standing facts in the wiki."""

from __future__ import annotations

from sqlmodel import Session

from app.domains.base import DomainHandler
from app.domains.health import areas as areas_svc
from app.domains.health import store
from app.config import settings
from app.domains.health.appointments import appointment_event, remove_appointment, sync_appointment
from app.domains.health.extraction import extract_health_document
from app.models.document import Document, DocumentStatus
from app.models.wiki import WikiOperation
from app.services.wiki_engine import ingest_into_wiki


class HealthHandler(DomainHandler):
    """Runs inside the privacy scope of the paper's type (always High): extraction and the wiki
    assessment both go out at house_high only."""

    async def process(self, session: Session, document: Document) -> Document:
        extracted = await extract_health_document(
            document.file_path, document.category,
            existing_areas=areas_svc.names_for_prompt(session, "area"),
            existing_conditions=areas_svc.names_for_prompt(session, "condition"))
        store.apply_extraction(session, document, extracted)
        await ingest_into_wiki(session, document, context=store.wiki_context(extracted),
                               operation=WikiOperation.INGEST)
        await sync_appointment(session, document)
        document.status = DocumentStatus.PROCESSED
        document.failure_reason = None
        session.add(document)
        session.commit()
        session.refresh(document)
        return document

    async def on_fields_changed(self, session: Session, document: Document, previous_fields: dict[str, str]) -> None:
        store.sync_entry_from_fields(session, document)
        await ingest_into_wiki(session, document, operation=WikiOperation.EDIT)
        await sync_appointment(session, document)
        # The date was cleared: there is no event any more, so the one already on the calendar must go too.
        if appointment_event(document, settings.PUBLIC_BASE_URL) is None:
            entry = store.entry_for(session, document.id)
            if entry is not None and entry.calendar_event_id:
                await remove_appointment(session, document, fields=previous_fields)

    async def withdraw(self, session: Session, document: Document) -> None:
        await remove_appointment(session, document)
        store.purge_derived(session, document)
