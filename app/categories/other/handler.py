"""Other papers handler: read the paper (title, date, about, summary), keeping what a person typed."""

from __future__ import annotations

from sqlmodel import Session

from app.categories.base import CategoryHandler
from app.categories.fields import dump_fields, load_fields
from app.models.document import Document, DocumentStatus
from app.services.document_input import is_model_readable

READ_FIELDS = ("title", "paper_date", "about", "summary")


class OtherHandler(CategoryHandler):
    async def process(self, session: Session, document: Document) -> Document:
        from app.categories.other import reader
        from app.services.paper_search import store_pdf_text
        fields = load_fields(document)
        if any(not fields.get(k) for k in READ_FIELDS) and is_model_readable(document.file_path):
            row = store_pdf_text(session, document)           # the text first: the reader uses it when there is one
            reading = await reader.read_other_paper(document.file_path, row.text if row is not None else None)
            found = {"title": reading.title, "about": reading.about, "summary": reading.summary,
                     "paper_date": reading.paper_date.isoformat() if reading.paper_date else ""}
            fields = {**{k: v for k, v in found.items() if v}, **{k: v for k, v in fields.items() if v}}
            if not fields.get("title"):
                fields["title"] = document.filename
            document.fields_json = dump_fields(fields)
        document.status = DocumentStatus.PROCESSED
        document.failure_reason = None
        session.add(document)
        session.commit()
        session.refresh(document)
        return document
