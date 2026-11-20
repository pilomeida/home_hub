"""A date on a paper that becomes a To-Do and, when the To-Do's day comes, one line on the phone (PLAN step 8).

A paper kind declares it (`KindSpec.reminder`): which of its date fields, how many days before, the To-Do's title and
the phone line. Both are read by the whole family, so they name whose and what kind of paper, never a number
(decision D21). Filing and editing a paper keep its To-Do in step (app/services/ingestion.py); a date unknown or past
makes none. Used by Identity & Legal (expiry), Vehicles (inspection, road tax, service), Education and Pets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Optional

from sqlmodel import Session

from app.categories.fields import field_date, load_fields
from app.models.document import Document
from app.models.todo import Todo


@dataclass(frozen=True)
class DateReminder:
    field: str                                        # the paper's ISO date field, e.g. "expires_on"
    days_before: int
    title: Callable[[Document, dict, date], str]      # the To-Do: "Renew Pedro's passport (expires 2027-03-02)"
    phone: Callable[[Document, dict, date], str]      # "Pedro's passport expires on 2027-03-02: see To-Dos."


def reminder_of(document: Document) -> Optional[DateReminder]:
    from app.categories.registry import get_spec, is_implemented
    from app.categories.base import UnknownKindError
    if not is_implemented(document.category) or not document.paper_kind:
        return None
    try:
        return get_spec(document.category).kind(document.paper_kind).reminder
    except UnknownKindError:
        return None


def first_name(name: str) -> str:
    return (name.split() or ["Someone"])[0].title()


def sync(session: Session, document: Document, today: Optional[date] = None) -> Optional[Todo]:
    """The paper's reminder To-Do, created or moved to its date; none when the kind has no reminder or the date is
    unknown or already past."""
    from app.services.todo_engine import upsert_document_todo
    reminder = reminder_of(document)
    if reminder is None:
        return None
    fields = load_fields(document)
    when = field_date(fields, reminder.field)
    if when is None or when < (today or date.today()):
        return None
    todo = upsert_document_todo(session, document, title=reminder.title(document, fields, when),
                                due_date=when - timedelta(days=reminder.days_before), category=document.category)
    session.commit()
    return todo


def phone_line(session: Session, todo: Todo) -> Optional[str]:
    """The phone line for a reminder To-Do whose day has come, or None when it is not a paper's date reminder."""
    document = session.get(Document, todo.document_id) if todo.document_id else None
    reminder = reminder_of(document) if document is not None else None
    if reminder is None:
        return None
    fields = load_fields(document)
    when = field_date(fields, reminder.field)
    return reminder.phone(document, fields, when) if when is not None else None
