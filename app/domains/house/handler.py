"""House domain handler: store-and-tag, plus the warranty reminder and the
item's wiki page. The only LLM use is extract_warranty_expiry, and only for
a warranty whose expiry was not entered by hand."""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session

from app.domains.base import DomainHandler
from app.domains.fields import dump_fields, field_date, load_fields
from app.domains.house.categories import HouseCategory
from app.domains.house.warranty import extract_warranty_expiry, sync_warranty_todo
from app.models.document import Document, DocumentStatus
from app.models.wiki import WikiOperation
from app.services.wiki_engine import ingest_into_wiki

MISSING_EXPIRY_NOTE = "No warranty expiry date found — enter it manually."


def _is_warranty_without_expiry(document: Document) -> bool:
    return (
        document.category == HouseCategory.WARRANTY_INVOICE.value
        and field_date(load_fields(document), "warranty_expiry") is None
    )


class HouseHandler(DomainHandler):
    async def process(self, session: Session, document: Document) -> Document:
        note: Optional[str] = None
        if _is_warranty_without_expiry(document):
            try:
                # Module-level lookup at call time so tests can monkeypatch it.
                expiry = await extract_warranty_expiry(document.file_path)
            except Exception as exc:
                # Best-effort by design: a failed extraction never fails the
                # upload -- the document is kept and the date asked for.
                expiry = None
                note = f"Warranty date could not be read ({exc}) — enter it manually."
            if expiry is not None:
                fields = load_fields(document)
                fields["warranty_expiry"] = expiry.isoformat()
                document.fields_json = dump_fields(fields)
                session.add(document)
                session.commit()
                session.refresh(document)
            elif note is None:
                note = MISSING_EXPIRY_NOTE

        await self._sync_derived(session, document, WikiOperation.INGEST)

        document.status = DocumentStatus.PROCESSED
        document.failure_reason = note
        session.add(document)
        session.commit()
        session.refresh(document)
        return document

    async def on_fields_changed(self, session: Session, document: Document, previous_fields: dict[str, str]) -> None:
        await self._sync_derived(session, document, WikiOperation.EDIT)
        if document.category == HouseCategory.WARRANTY_INVOICE.value and not _is_warranty_without_expiry(document):
            document.failure_reason = None
            session.add(document)
            session.commit()

    async def _sync_derived(self, session: Session, document: Document, operation: WikiOperation) -> None:
        sync_warranty_todo(session, document)
        await ingest_into_wiki(session, document, operation=operation)
