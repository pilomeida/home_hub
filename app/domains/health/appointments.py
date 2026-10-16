"""Appointments on the family Google Calendar. The event is built ONLY from administrative fields
(app/privacy/channels.channel_fields): when, where, with whom, how to reach them, a reminder and a link back
to the paper in the Hub. The reason for the visit, the kind of event, the summary and the areas never go."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlmodel import Session

from app.config import settings
from app.domains.fields import load_fields
from app.domains.health import store
from app.domains.registry import get_spec
from app.integrations.google_calendar import GoogleCalendar
from app.models.document import Document
from app.models.domain import Domain
from app.privacy.channels import channel_fields
from app.problems import Problem, explain, record_problem, resolve_for

logger = logging.getLogger("health.appointments")
_TZ = "Europe/Lisbon"
_DEFAULT_MINUTES = 30


def _today() -> date:
    return datetime.now(ZoneInfo(_TZ)).date()


def _administrative(document: Document, fields: Optional[dict] = None) -> dict:
    """The fields an event may show, from `fields` (default: the paper's current ones)."""
    spec = get_spec(Domain.HEALTH)
    return channel_fields(spec, document.category, fields if fields is not None else load_fields(document))


def event_title(document: Document, fields: Optional[dict] = None) -> tuple[str, str]:
    """(title, when) as the Hub names the calendar entry, e.g. ("Maria: Clínica Sul", "2026-11-03 at 10:30")."""
    shown = _administrative(document, fields)
    title = f"{shown.get('person') or 'Family'}: {shown.get('provider') or 'Appointment'}"
    day = shown.get("appointment_date") or ""
    time = (shown.get("appointment_time") or "").strip()
    return title, f"{day} at {time}" if time else day


def appointment_event(document: Document, public_url: str, today: Optional[date] = None) -> Optional[dict]:
    """The calendar entry for an appointment paper, or None when it has none to make: not an appointment, no
    date, or a date already past (old papers filed later, e.g. the backfill, must not fill the past of the calendar)."""
    if document.category != "appointment":
        return None
    fields = _administrative(document)
    day = fields.get("appointment_date")
    if not day:
        return None
    try:
        if date.fromisoformat(day) < (today or _today()):
            return None
    except ValueError:
        pass                                    # an unreadable date is handled below as before
    title, _ = event_title(document)
    lines = [f"Open the paper in the Hub: {public_url.rstrip('/')}/health/documents/{document.id}"]
    if fields.get("provider_phone"):
        lines.insert(0, f"Phone: {fields['provider_phone']}")
    body: dict = {"summary": title, "description": "\n".join(lines),
                  "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 1440},
                                                                   {"method": "popup", "minutes": 120}]}}
    if fields.get("provider_address"):
        body["location"] = fields["provider_address"]
    time = (fields.get("appointment_time") or "").strip()
    try:
        start = datetime.fromisoformat(f"{day}T{time}:00") if time else None
    except ValueError:
        start = None
    if start is not None:
        body["start"] = {"dateTime": start.isoformat(), "timeZone": _TZ}
        body["end"] = {"dateTime": (start + timedelta(minutes=_DEFAULT_MINUTES)).isoformat(), "timeZone": _TZ}
    else:
        day_date = datetime.fromisoformat(day).date()
        body["start"] = {"date": day_date.isoformat()}
        body["end"] = {"date": (day_date + timedelta(days=1)).isoformat()}
    return body


def _key(document: Document) -> str:
    return f"health-doc-{document.id}"


async def sync_appointment(session: Session, document: Document, calendar: Optional[GoogleCalendar] = None,
                           public_url: Optional[str] = None, today: Optional[date] = None) -> Optional[str]:
    """Create or update the calendar entry. Never raises: the paper is filed either way, and a failure is
    kept on the Problems page in the three-part form. A past appointment gets no entry; an entry it already
    has is left as it is."""
    body = appointment_event(document, public_url or settings.PUBLIC_BASE_URL, today=today)
    if body is None:
        return None
    ref = f"calendar:{document.id}"
    calendar = calendar or GoogleCalendar.from_settings(settings)
    if calendar is None:
        record_problem(session, area="Calendar", ref=ref, problem=Problem(
            "Not added to the calendar.", "Google Calendar access is not set up yet, so the Hub cannot write to it.",
            "Ask Pedro to finish the calendar set-up (docs/SYSADMIN.md, Google Calendar), then edit this "
            "appointment's date to add it.", link=f"/health/documents/{document.id}"))
        return None
    try:
        event_id = await calendar.upsert_event(_key(document), body)
    except Exception as exc:
        logger.exception("calendar sync failed for document %s", document.id)
        problem = explain("Not added to the calendar.", str(exc))
        record_problem(session, area="Calendar", ref=ref,
                       problem=Problem(problem.what, problem.why, problem.next_step, f"/health/documents/{document.id}"),
                       technical=str(exc))
        return None
    entry = store.entry_for(session, document.id)
    if entry is not None:
        entry.calendar_event_id = event_id
        session.add(entry)
        session.commit()
    resolve_for(session, ref)
    return event_id


async def remove_appointment(session: Session, document: Document, calendar: Optional[GoogleCalendar] = None,
                             *, fields: Optional[dict] = None) -> None:
    """Delete the calendar entry. `fields` are the values the entry was last made from (a date change passes the
    previous ones, since the cleared paper no longer names an event). If the delete fails, the Problem names the
    entry by its title and date so the person can remove it by hand; the event id is kept in the technical detail."""
    entry = store.entry_for(session, document.id)
    if entry is None or not entry.calendar_event_id:
        return
    calendar = calendar or GoogleCalendar.from_settings(settings)
    if calendar is None:
        return
    event_id = entry.calendar_event_id
    title, when = event_title(document, fields)
    try:
        await calendar.delete_event(event_id)
    except Exception as exc:
        record_problem(session, area="Calendar", ref=f"calendar:{document.id}", problem=Problem(
            "Not removed from the calendar.",
            "Google Calendar did not delete the entry, so it is still on the family calendar.",
            f"Open the family Google Calendar, find \"{title}\" on {when}, and delete that entry by hand."),
            technical=f"Calendar event id {event_id}: {exc}")
        return
    entry.calendar_event_id = None
    session.add(entry)
    session.commit()
