"""One handler for categories of typed papers (PLAN step 8: Education, Pets): read the empty details from the paper,
put whom or what it is about in the subjects registry under the name it keeps, and say on the paper when the date its
kind reminds about was not found. The kind's reminder itself is app/categories/reminders.py."""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session

from app.categories.base import CategoryHandler
from app.categories.fields import dump_fields, load_fields
from app.categories.paper_reading import fill_empty
from app.categories.reminders import reminder_of
from app.models.document import Document, DocumentStatus


class TypedPaperHandler(CategoryHandler):
    def __init__(self, system: str, keys: tuple[str, ...], dates: tuple[str, ...], subject_field: str,
                 subject_kind, date_words: dict[str, str], *, expects_date: frozenset[str] = frozenset(),
                 choices: dict[str, set[str]] | None = None):
        self.system, self.keys, self.dates = system, keys, dates
        self.subject_field, self.subject_kind, self.date_words = subject_field, subject_kind, date_words
        self.expects_date = expects_date            # the kinds whose paper should carry its reminder's date
        self.choices = choices or {}                # a read value outside a field's choices is dropped

    def _settle(self, session: Session, fields: dict) -> dict:
        from app.services import subjects
        for key, allowed in self.choices.items():
            if fields.get(key) and fields[key] not in allowed:
                fields.pop(key)
        name = (fields.get(self.subject_field) or "").strip()
        subject = subjects.register(session, self.subject_kind, name) if name else None
        if subject is not None:
            fields[self.subject_field] = subject.name
        return fields

    def _note(self, document: Document, fields: dict) -> Optional[str]:
        reminder = reminder_of(document)
        if reminder is None or document.paper_kind not in self.expects_date or fields.get(reminder.field):
            return None
        words = self.date_words.get(reminder.field, "the date")
        return f"{words[0].upper()}{words[1:]} was not found on the paper. Type it in the details below and save."

    async def process(self, session: Session, document: Document) -> Document:
        fields = await fill_empty(session, document, load_fields(document), self.system, self.keys, self.dates)
        fields = self._settle(session, fields)
        document.fields_json = dump_fields(fields)
        document.status = DocumentStatus.PROCESSED
        document.failure_reason = self._note(document, fields)
        session.add(document)
        session.commit()
        session.refresh(document)
        return document

    async def on_fields_changed(self, session: Session, document: Document, previous_fields: dict[str, str]) -> None:
        fields = self._settle(session, load_fields(document))
        document.fields_json = dump_fields(fields)
        document.failure_reason = self._note(document, fields)
        session.add(document)
        session.commit()
