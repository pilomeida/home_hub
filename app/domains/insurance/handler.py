"""Insurance domain handler: store-and-tag plus the policy's wiki page. No LLM."""

from __future__ import annotations

from sqlmodel import Session

from app.domains.base import DomainHandler
from app.models.document import Document, DocumentStatus
from app.models.wiki import WikiOperation
from app.services.wiki_engine import ingest_into_wiki


class InsuranceHandler(DomainHandler):
    async def process(self, session: Session, document: Document) -> Document:
        await ingest_into_wiki(session, document, operation=WikiOperation.INGEST)
        document.status = DocumentStatus.PROCESSED
        document.failure_reason = None
        session.add(document)
        session.commit()
        session.refresh(document)
        return document

    async def on_fields_changed(self, session: Session, document: Document, previous_fields: dict[str, str]) -> None:
        await ingest_into_wiki(session, document, operation=WikiOperation.EDIT)
