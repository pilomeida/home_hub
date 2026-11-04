"""Health domain handler: read the paper, store what it says, put the standing facts in the wiki."""

from __future__ import annotations

from sqlmodel import Session

from app.categories.base import CategoryHandler
from app.categories.health import areas as areas_svc
from app.categories.health import outlines, store
from app.config import settings
from app.categories.health.appointments import appointment_event, remove_appointment, sync_appointment
from app.categories.health.extraction import HealthExtractionError
from app.categories.health.reading import read_health_document
from app.models.document import Document, DocumentStatus
from app.models.wiki import WikiOperation
from app.privacy.settings import configured_tier
from app.problems import HEALTH_BILL_NEXT_STEP
from app.services.wiki_engine import ingest_into_wiki


class HealthHandler(CategoryHandler):
    """Runs inside the privacy scope of the paper's type (always High): extraction and the wiki
    assessment both go out at house_high only."""

    async def process(self, session: Session, document: Document) -> Document:
        extracted = await read_health_document(
            document.file_path, document.paper_kind,
            existing_areas=areas_svc.names_for_prompt(session, "area"),
            existing_conditions=areas_svc.names_for_prompt(session, "condition"),
            guidance=outlines.guidance(session))
        if extracted.is_bill:               # a bill, receipt or reimbursement statement belongs under Bills, not Health
            raise HealthExtractionError(
                "This looks like a bill or receipt, so it is not filed under Health. "
                "Bills are dealt with under Bills, like any other bill. " + HEALTH_BILL_NEXT_STEP)
        if document.paper_kind is None:       # the upload asked only for the file: the reading proposes the kind of paper
            if extracted.paper_kind is None:
                raise HealthExtractionError(
                    "The Hub could not tell what kind of health paper this is (or whether it is one). "
                    "Choose the kind on the paper's page, or delete it.")
            document.paper_kind = extracted.paper_kind
            document.sensitivity_tier = configured_tier(session, document.category, document.paper_kind)
            session.add(document)
            session.commit()
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
