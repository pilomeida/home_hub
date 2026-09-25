"""One shape for everything a domain holds: documents (uploaded files) and
records (hand-entered, optionally with an attached file). Listings, cards and
Plan C's Ask read domain_entries() so both kinds are treated uniformly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from app.domains.base import SourceKind
from app.domains.fields import load_fields
from app.domains.registry import document_url, get_spec, is_implemented, record_url
from app.models.document import Document, DocumentStatus
from app.models.domain import Domain
from app.models.record import Record


@dataclass
class SourceEntry:
    kind: SourceKind
    category: Optional[str]
    fields: dict[str, str]
    created_at: datetime
    document: Optional[Document]   # the file: the document itself, or a record's attachment
    record: Optional[Record]
    url: Optional[str]

    @property
    def title(self) -> str:
        if self.document is not None:
            return self.document.filename
        domain = self.record.domain if self.record is not None else None
        label = get_spec(domain).category_label(self.category) if is_implemented(domain) else (self.category or "")
        return f"{label} — entered by hand"


def record_for_document(session: Session, document: Document) -> Optional[Record]:
    return session.exec(
        select(Record).where(Record.document_id == document.id, Record.retired_at.is_(None))
    ).first()


def domain_entries(session: Session, domain: Domain) -> list[SourceEntry]:
    records = list(session.exec(
        select(Record).where(Record.domain == domain, Record.retired_at.is_(None))
    ).all())
    attached_ids = {r.document_id for r in records if r.document_id is not None}
    attachments = {}
    if attached_ids:
        attachments = {d.id: d for d in session.exec(select(Document).where(Document.id.in_(attached_ids))).all()}
    documents = session.exec(
        select(Document).where(
            Document.domain == domain,
            Document.status.in_([DocumentStatus.PROCESSED, DocumentStatus.NEEDS_ATTENTION]),
        )
    ).all()

    entries = [
        SourceEntry(SourceKind.DOCUMENT, d.category, load_fields(d), d.created_at, d, None, document_url(d))
        for d in documents if d.id not in attached_ids
    ]
    entries += [
        SourceEntry(SourceKind.RECORD, r.category, load_fields(r), r.created_at,
                    attachments.get(r.document_id), r, record_url(r))
        for r in records
    ]
    return sorted(entries, key=lambda e: (e.created_at, e.kind.value, (e.record or e.document).id))
