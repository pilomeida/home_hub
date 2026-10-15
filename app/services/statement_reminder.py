"""The 5-monthly "download the latest bank statements" reminder.

The cycle is anchored on the newest position data we hold (statement snapshots only;
loan-history printouts never postpone it): five calendar months later the reminder is due, and the
user then has 30 days. One ReminderLog row per cycle makes the push once-only."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from sqlmodel import Session, func, select

from app.problems import Problem

from app.models.position import (
    BalanceSnapshot, LoanSnapshot, ReminderLog, SavingsSnapshot,
)

REMINDER_TEXT = (
    "Reminder: to keep loan and savings data updated, you have 30 days to download "
    "the latest digital bank statements from Santander and share them with me."
)
UPLOAD_URL = "/financials/loans/upload"
REMINDER_KEY = "statement_positions"
CYCLE_MONTHS = 5
GRACE_DAYS = 30


@dataclass
class ReminderState:
    due: bool
    overdue: bool
    cycle_start: Optional[date]
    deadline: Optional[date]
    last_positions: Optional[date]


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _last_positions(session: Session) -> Optional[date]:
    newest = [
        session.exec(select(func.max(column))).one()
        for column in (LoanSnapshot.as_of, SavingsSnapshot.as_of, BalanceSnapshot.as_of)  # NOT movements: a printout is not a statement
    ]
    newest = [d for d in newest if d is not None]
    return max(newest) if newest else None


def get_statement_reminder(session: Session, today: date) -> ReminderState:
    last = _last_positions(session)
    if last is None:
        return ReminderState(False, False, None, None, None)
    cycle_start = _add_months(last, CYCLE_MONTHS)
    deadline = cycle_start + timedelta(days=GRACE_DAYS)
    return ReminderState(today >= cycle_start, today > deadline, cycle_start, deadline, last)


def reminder_to_send(session: Session, today: date) -> bool:
    state = get_statement_reminder(session, today)
    if not state.due:
        return False
    logged = session.exec(
        select(ReminderLog).where(ReminderLog.key == REMINDER_KEY, ReminderLog.cycle_start == state.cycle_start)
    ).first()
    return logged is None


def mark_reminder_sent(session: Session, today: date) -> None:
    state = get_statement_reminder(session, today)
    if state.cycle_start is None:
        return
    exists = session.exec(
        select(ReminderLog).where(ReminderLog.key == REMINDER_KEY, ReminderLog.cycle_start == state.cycle_start)
    ).first()
    if exists is None:
        session.add(ReminderLog(key=REMINDER_KEY, sent_at=datetime.utcnow(), cycle_start=state.cycle_start))
        session.commit()


# --- when the reminder can't be delivered (decision D14) ---------------------------------------------
PROBLEM_AREA = "Reminders"
PROBLEM_REF = "statement-reminder"


def problem_no_token() -> Problem:
    return Problem(
        "The statement reminder wasn't sent.",
        "The Hub has no Telegram bot set up on the server, so it has nothing to send the message with.",
        "Tell Pedro to add the Telegram bot token to the server settings; the reminder goes out on the "
        "next daily run (09:00) after that.")


def problem_bad_user_list() -> Problem:
    return Problem(
        "The statement reminder wasn't sent.",
        "The list of people allowed to use the Hub's Telegram bot can't be read, so the Hub doesn't know "
        "who to send the reminder to.",
        "Tell Pedro to correct the allowed Telegram users in the server settings; the reminder goes out on "
        "the next daily run (09:00) after that.")


def problem_no_recipient(name: str) -> Problem:
    return Problem(
        "The statement reminder wasn't sent.",
        f"No one named “{name}” is in the list of people allowed to use the Hub's Telegram bot, "
        "so the Hub doesn't know who to send it to.",
        "Tell Pedro to make the reminder's name match one of the allowed Telegram users in the server "
        "settings; the reminder goes out on the next daily run (09:00) after that.")


def problem_send_failed() -> Problem:
    return Problem(
        "The statement reminder wasn't sent.",
        "Telegram didn't accept the message, usually because it couldn't be reached for a moment.",
        "Nothing is lost: the Hub tries again on the next daily run (09:00). If it is still failing "
        "in a few days, tell Pedro: the technical details are on the Problems page.",
        "/problems")
