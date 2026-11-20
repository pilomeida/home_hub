"""Visits, notes and medication on a condition (PLAN step 7c), and its one timeline: papers, visits, notes,
medication changes and follow-ups, newest first. Everything here is typed by a person; who wrote it and when is kept
(family-open, Pedro 2026-10-19 Q10). A booked visit does not reach the calendar yet (Google Calendar for Health
waits on Pedro, section 2 of PLAN.md); the reason for a visit never would."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.categories.health import conditions as conditions_svc
from app.categories.health import follow_ups as follow_ups_svc
from app.categories.health.kinds import KIND_LABELS
from app.models.health import (HealthCondition, HealthMedication, HealthMedicationChange, HealthNote, HealthVisit)
from app.problems import Problem

VISIT_STATUSES = ("booked", "done")
NOTE_KINDS = {"note": "Note", "reading": "Reading", "episode": "Episode"}


class CareError(Exception):
    def __init__(self, problem: Problem):
        super().__init__(problem.text)
        self.problem = problem


def _condition(session: Session, condition_id: int) -> HealthCondition:
    condition = session.get(HealthCondition, condition_id)
    if condition is None:
        raise CareError(Problem("Not saved.", "That condition is no longer listed.", "Reload the page."))
    return condition


def _need(value, what: str):
    if value is None or (isinstance(value, str) and not value.strip()):
        raise CareError(Problem("Not saved.", f"{what} is missing.", f"Fill in {what.lower()}, then save again."))
    return value.strip() if isinstance(value, str) else value


def add_visit(session: Session, condition_id: int, *, visit_on: Optional[date], clinic: str = "", doctor: str = "",
              status: str = "booked", observations: str = "", plan_changes: str = "",
              next_appointment_on: Optional[date] = None, by: Optional[str] = None) -> HealthVisit:
    condition = _condition(session, condition_id)
    _need(visit_on, "The date of the visit")
    if status not in VISIT_STATUSES:
        raise CareError(Problem("Not saved.", f"“{status}” is not a visit's state.", "Choose booked or done."))
    visit = HealthVisit(person_id=condition.person_id, condition_id=condition.id, status=status, visit_on=visit_on,
                        clinic=clinic.strip() or None, doctor=doctor.strip() or None,
                        observations=observations.strip() or None, plan_changes=plan_changes.strip() or None,
                        next_appointment_on=next_appointment_on, created_by=by)
    session.add(visit)
    session.commit()
    session.refresh(visit)
    return visit


def visit_done(session: Session, visit_id: int, *, observations: str, plan_changes: str,
               next_appointment_on: Optional[date]) -> HealthVisit:
    """A booked visit happened: what was seen, what changed, and the next one (which is then booked too)."""
    visit = session.get(HealthVisit, visit_id)
    if visit is None:
        raise CareError(Problem("Not saved.", "That visit is no longer listed.", "Reload the page."))
    visit.status, visit.observations = "done", observations.strip() or None
    visit.plan_changes, visit.next_appointment_on = plan_changes.strip() or None, next_appointment_on
    session.add(visit)
    if next_appointment_on is not None and visit.condition_id is not None:
        session.add(HealthVisit(person_id=visit.person_id, condition_id=visit.condition_id, status="booked",
                                visit_on=next_appointment_on, clinic=visit.clinic, doctor=visit.doctor,
                                created_by=visit.created_by))
    session.commit()
    return visit


def add_note(session: Session, condition_id: int, *, noted_on: Optional[date], kind: str, text: str,
             value: Optional[float] = None, unit: str = "", by: Optional[str] = None) -> HealthNote:
    condition = _condition(session, condition_id)
    _need(noted_on, "The date")
    if kind not in NOTE_KINDS:
        raise CareError(Problem("Not saved.", f"“{kind}” is not a kind of note.", "Choose note, reading or episode."))
    if kind == "reading" and value is None:
        raise CareError(Problem("Not saved.", "A reading needs its value.", "Type the number you read, then save."))
    if kind != "reading":
        _need(text, "The note")
    note = HealthNote(person_id=condition.person_id, condition_id=condition.id, kind=kind, noted_on=noted_on,
                      text=(text or "").strip(), value=value, unit=unit.strip() or None, created_by=by)
    session.add(note)
    session.commit()
    session.refresh(note)
    return note


def add_medication(session: Session, condition_id: int, *, name: str, dose: str = "", frequency: str = "",
                   started_on: Optional[date] = None, by: Optional[str] = None) -> HealthMedication:
    condition = _condition(session, condition_id)
    med = HealthMedication(person_id=condition.person_id, condition_id=condition.id, name=_need(name, "The medicine"),
                           dose=dose.strip() or None, frequency=frequency.strip() or None,
                           started_on=started_on, created_by=by)
    session.add(med)
    session.flush()
    session.add(HealthMedicationChange(medication_id=med.id, changed_on=started_on or date.today(), change="new",
                                       dose=med.dose, frequency=med.frequency, created_by=by))
    session.commit()
    session.refresh(med)
    return med


def change_medication(session: Session, medication_id: int, *, changed_on: Optional[date], dose: str = "",
                      frequency: str = "", stop: bool = False, visit_id: Optional[int] = None,
                      by: Optional[str] = None) -> HealthMedication:
    """A new dose or frequency, or stopped. The change is added; what it was stays in the history."""
    med = session.get(HealthMedication, medication_id)
    if med is None:
        raise CareError(Problem("Not saved.", "That medicine is no longer listed.", "Reload the page."))
    if med.stopped_on is not None:
        raise CareError(Problem("Not saved.", f"{med.name} was already stopped on {med.stopped_on.isoformat()}.",
                                "Add it again as a new medicine if it was started again."))
    when = _need(changed_on, "The date of the change")
    if stop:
        med.stopped_on = when
    else:
        med.dose, med.frequency = dose.strip() or med.dose, frequency.strip() or med.frequency
    session.add(med)
    session.add(HealthMedicationChange(medication_id=med.id, changed_on=when, change="stopped" if stop else "changed",
                                       dose=None if stop else med.dose, frequency=None if stop else med.frequency,
                                       visit_id=visit_id, created_by=by))
    session.commit()
    return med


def medications(session: Session, condition_id: int) -> list[HealthMedication]:
    return list(session.exec(select(HealthMedication).where(HealthMedication.condition_id == condition_id)
                             .order_by(HealthMedication.stopped_on.is_not(None), HealthMedication.name)).all())


def visits(session: Session, condition_id: int, status: Optional[str] = None) -> list[HealthVisit]:
    q = select(HealthVisit).where(HealthVisit.condition_id == condition_id)
    if status:
        q = q.where(HealthVisit.status == status)
    return list(session.exec(q.order_by(HealthVisit.visit_on)).all())


@dataclass
class Event:
    when: Optional[date]
    kind: str            # "paper" | "visit" | "booked" | "note" | "reading" | "episode" | "medication" | "follow-up"
    title: str
    detail: str
    link: Optional[str] = None
    ahead: bool = False


def timeline(session: Session, condition_id: int, today: Optional[date] = None) -> list[Event]:
    """Everything about the condition on one line of time, newest (or furthest ahead) first."""
    today = today or date.today()
    out: list[Event] = []
    for e in conditions_svc.papers_of(session, condition_id):
        out.append(Event(e.entry_date, "paper", KIND_LABELS.get(e.kind or "", "Paper"), e.provider or "",
                         f"/health/documents/{e.document_id}"))
    for v in visits(session, condition_id):
        where = " · ".join(filter(None, [v.clinic, v.doctor]))
        if v.status == "booked":
            out.append(Event(v.visit_on, "booked", "Visit booked", where, ahead=v.visit_on >= today))
        else:
            out.append(Event(v.visit_on, "visit", "Visit", " — ".join(filter(None, [where, v.observations,
                                                                                     v.plan_changes]))))
    for n in session.exec(select(HealthNote).where(HealthNote.condition_id == condition_id)).all():
        reading = f"{n.value:g}{(' ' + n.unit) if n.unit else ''}" if n.value is not None else ""
        out.append(Event(n.noted_on, n.kind, NOTE_KINDS[n.kind], " ".join(filter(None, [reading, n.text]))))
    for med in medications(session, condition_id):
        for c in session.exec(select(HealthMedicationChange).where(HealthMedicationChange.medication_id == med.id)).all():
            what = {"new": "Started", "changed": "Changed", "stopped": "Stopped"}[c.change]
            out.append(Event(c.changed_on, "medication", f"{what} {med.name}",
                             " ".join(filter(None, [c.dose, c.frequency]))))
    for due in follow_ups_svc.open_ones(session, today, condition_id=condition_id):
        out.append(Event(due.follow_up.due_on, "follow-up", "Follow-up due", due.follow_up.what, ahead=True))
    return sorted(out, key=lambda ev: ev.when or date.min, reverse=True)


__all__ = ["CareError", "VISIT_STATUSES", "NOTE_KINDS", "add_visit", "visit_done", "add_note", "add_medication",
           "change_medication", "medications", "visits", "Event", "timeline"]
