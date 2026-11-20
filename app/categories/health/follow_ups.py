"""Health follow-ups and reminders (PLAN step 7b): something to do again by a date ("repeat the MRI in 12 months").

- A person adds one on a condition's page (or a person's); its reminder is a Health To-Do REMIND_BEFORE days before
  it is due, so it shows on To-Dos, Coming up and Health's follow-ups, overdue in red, and reaches the phone once
  through the notices (with no clinical words: decision D21).
- A paper's own words ("repetir em 12 meses", "controlo dentro de 6 meses", "repeat in 1 year") propose one, dated
  from the paper; a person accepts it or says no (the Hub only proposes). Code reads the words, never a model.
- It is done when its To-Do is done."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models.category import Category
from app.models.health import HealthCondition, HealthEntry, HealthEntryCondition, HealthFollowUp, HealthPerson
from app.models.paper_text import PaperText
from app.models.todo import Todo
from app.problems import Problem
from app.services.loan_math import add_months

REMIND_BEFORE = 14                      # days: the To-Do comes this long before the follow-up is due
PROPOSED, ACTIVE, DISMISSED = "proposed", "active", "dismissed"

_NUM = r"(\d{1,2}|um|uma|dois|duas|tres|três|seis|doze|one|two|three|six|twelve)"
_WORDS = {"um": 1, "uma": 1, "one": 1, "dois": 2, "duas": 2, "two": 2, "tres": 3, "três": 3, "three": 3, "seis": 6,
          "six": 6, "doze": 12, "twelve": 12}
# "repetir (o exame) em 12 meses", "controlo dentro de 6 meses", "reavaliação daqui a 1 ano", "repeat in 1 year"
_PROPOSE = re.compile(
    r"\b(repetir|repetição|controlo|controle|reavaliar|reavaliação|nova avaliação|seguimento|revisão|repeat|follow[- ]up|"
    r"recheck|review)\b[^.\n]{0,40}?\b(?:em|dentro de|daqui a|após|apos|in|within|after)\s+" + _NUM +
    r"\s*(meses|mês|mes|anos|ano|semanas|months?|years?|weeks?)\b", re.I)


class FollowUpError(Exception):
    def __init__(self, problem: Problem):
        super().__init__(problem.text)
        self.problem = problem


def _months(number: str, unit: str) -> Optional[float]:
    n = int(number) if number.isdigit() else _WORDS.get(number.lower())
    if not n:
        return None
    unit = unit.lower()
    if unit.startswith(("ano", "year")):
        return n * 12
    if unit.startswith(("semana", "week")):
        return n / 4.345
    return n


def _due(start: date, months: float) -> date:
    if months == int(months):
        return add_months(start, int(months))
    return start + timedelta(days=round(months * 30.44))


def _person_name(session: Session, person_id: int) -> str:
    p = session.get(HealthPerson, person_id)
    return p.name if p else "someone"


def _remind(session: Session, f: HealthFollowUp) -> Todo:
    """The follow-up's To-Do: its title names what and for whom (the To-Do list is family-open), never the condition."""
    entry = session.get(HealthEntry, f.entry_id) if f.entry_id else None
    title = f"{f.what} — {_person_name(session, f.person_id)} (due {f.due_on.isoformat()})"
    todo = session.get(Todo, f.todo_id) if f.todo_id else None
    if todo is None or todo.done:
        todo = Todo(title=title, category=Category.HEALTH, document_id=entry.document_id if entry else None)
    todo.title, todo.due_date = title, f.due_on - timedelta(days=REMIND_BEFORE)
    session.add(todo)
    session.flush()
    f.todo_id = todo.id
    session.add(f)
    return todo


def add(session: Session, person_id: int, what: str, due_on: Optional[date], by: Optional[str], *,
        condition_id: Optional[int] = None) -> HealthFollowUp:
    if not (what or "").strip() or due_on is None:
        raise FollowUpError(Problem("Not added.", "A follow-up needs what to do and the date it is due.",
                                    "Fill in both, then press Add."))
    if condition_id is not None:
        condition = session.get(HealthCondition, condition_id)
        if condition is None:
            raise FollowUpError(Problem("Not added.", "That condition is no longer listed.", "Reload the page."))
        person_id = condition.person_id
    f = HealthFollowUp(person_id=person_id, condition_id=condition_id, what=what.strip(), due_on=due_on,
                       source="person", state=ACTIVE, created_by=by)
    session.add(f)
    session.flush()
    _remind(session, f)
    session.commit()
    session.refresh(f)
    return f


def propose_from_paper(session: Session, entry: HealthEntry) -> int:
    """Read the paper's words for "repeat in N months" and propose a follow-up dated from the paper; one per wording,
    never again once a person decided. Returns how many were proposed."""
    if entry.person_id is None or entry.entry_date is None:
        return 0
    text = session.exec(select(PaperText.text).where(PaperText.document_id == entry.document_id)).first() or ""
    text = " ".join(filter(None, [text, entry.summary or ""]))
    condition = session.exec(select(HealthEntryCondition.condition_id).where(
        HealthEntryCondition.entry_id == entry.id, HealthEntryCondition.state == "confirmed")).first()
    added = 0
    for m in _PROPOSE.finditer(text):
        months = _months(m.group(2), m.group(3))
        if months is None:
            continue
        words = re.sub(r"\s+", " ", m.group(0)).strip()
        known = session.exec(select(HealthFollowUp).where(HealthFollowUp.entry_id == entry.id,
                                                          HealthFollowUp.source_text == words)).first()
        if known is not None:
            continue
        session.add(HealthFollowUp(person_id=entry.person_id, condition_id=condition, what=words[:1].upper() + words[1:],
                                   due_on=_due(entry.entry_date, months), source="paper", state=PROPOSED,
                                   entry_id=entry.id, source_text=words))
        added += 1
    session.flush()
    return added


def accept(session: Session, follow_up_id: int, by: Optional[str]) -> HealthFollowUp:
    f = session.get(HealthFollowUp, follow_up_id)
    if f is None or f.state != PROPOSED:
        raise FollowUpError(Problem("Not saved.", "That proposal was already answered, or is no longer there.",
                                    "Reload the page to see what is still waiting."))
    f.state, f.created_by = ACTIVE, by
    _remind(session, f)
    session.commit()
    return f


def dismiss(session: Session, follow_up_id: int) -> None:
    f = session.get(HealthFollowUp, follow_up_id)
    if f is None:
        raise FollowUpError(Problem("Not saved.", "That follow-up is no longer there.", "Reload the page."))
    f.state = DISMISSED
    if f.todo_id:
        todo = session.get(Todo, f.todo_id)
        if todo is not None and not todo.done:
            session.delete(todo)
        f.todo_id = None
    session.add(f)
    session.commit()


def is_done(session: Session, f: HealthFollowUp) -> bool:
    todo = session.get(Todo, f.todo_id) if f.todo_id else None
    return bool(todo and todo.done)


@dataclass
class Due:
    follow_up: HealthFollowUp
    note: str               # "in 12 days" / "today" / "overdue"
    late: bool


def _note(due: date, today: date) -> tuple[str, bool]:
    days = (due - today).days
    if days < 0:
        return "overdue", True
    return ("today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}"), False


def open_ones(session: Session, today: date, *, person_id: Optional[int] = None,
              condition_id: Optional[int] = None) -> list[Due]:
    """The active follow-ups not done yet, soonest first (the first is the "next due")."""
    q = select(HealthFollowUp).where(HealthFollowUp.state == ACTIVE)
    if person_id is not None:
        q = q.where(HealthFollowUp.person_id == person_id)
    if condition_id is not None:
        q = q.where(HealthFollowUp.condition_id == condition_id)
    rows = [f for f in session.exec(q).all() if not is_done(session, f)]
    return [Due(f, *_note(f.due_on, today)) for f in sorted(rows, key=lambda f: f.due_on)]


def proposals(session: Session) -> list[HealthFollowUp]:
    return list(session.exec(select(HealthFollowUp).where(HealthFollowUp.state == PROPOSED)
                             .order_by(HealthFollowUp.due_on)).all())


def forget_entry(session: Session, entry_id: int) -> None:
    """A paper deleted: its proposals go; an accepted follow-up stays (a person decided it), without the paper."""
    for f in session.exec(select(HealthFollowUp).where(HealthFollowUp.entry_id == entry_id)).all():
        if f.state == PROPOSED:
            session.delete(f)
        else:
            f.entry_id = None
            session.add(f)


__all__ = ["REMIND_BEFORE", "FollowUpError", "add", "propose_from_paper", "accept", "dismiss", "is_done", "Due",
           "open_ones", "proposals", "forget_entry"]
