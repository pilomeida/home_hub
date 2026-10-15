"""When is a scheduled job due? Pure functions over a JobSchedule and the time of the last
SCHEDULED run (manual runs never move the cycle). All wall-clock times are Europe/Lisbon.

A job is due when its most recent planned moment has passed without a run since. So a run missed
because the server was off happens once, at the next check — never several times over."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from app.models.schedule import JobSchedule

LISBON = ZoneInfo("Europe/Lisbon")
KINDS = {
    "daily": "Every day",
    "every_n_days": "Every few days",
    "weekly": "Every week, on a chosen day",
    "every_n_weeks": "Every few weeks, on a chosen day",
    "monthly": "Every month, on a chosen date",
}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class InvalidSchedule(ValueError):
    pass


@dataclass(frozen=True)
class Spec:
    kind: str = "weekly"
    every_n: int = 1
    weekday: int = 6
    day_of_month: int = 1
    time_of_day: str = "04:30"
    enabled: bool = True


def spec_of(row: JobSchedule) -> Spec:
    return Spec(row.kind, row.every_n, row.weekday, row.day_of_month, row.time_of_day, row.enabled)


def validate(spec: Spec) -> Spec:
    if spec.kind not in KINDS:
        raise InvalidSchedule("Choose how often the job should run.")
    try:
        h, m = (int(x) for x in spec.time_of_day.split(":"))
        time(h, m)
    except Exception:
        raise InvalidSchedule("Enter the time as HH:MM, for example 04:30.") from None
    if spec.kind in ("every_n_days", "every_n_weeks") and not 1 <= spec.every_n <= 365:
        raise InvalidSchedule("Enter a number between 1 and 365.")
    if spec.kind in ("weekly", "every_n_weeks") and not 0 <= spec.weekday <= 6:
        raise InvalidSchedule("Choose a day of the week.")
    if spec.kind == "monthly" and not 1 <= spec.day_of_month <= 31:
        raise InvalidSchedule("Choose a day of the month between 1 and 31.")
    return spec


def _at(d: date, spec: Spec) -> datetime:
    h, m = (int(x) for x in spec.time_of_day.split(":"))
    return datetime.combine(d, time(h, m), tzinfo=LISBON)


def _month_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def latest_slot(spec: Spec, now: datetime) -> datetime:
    """The most recent planned moment at or before `now` (for the calendar-based kinds)."""
    today = now.date()
    if spec.kind in ("daily", "every_n_days"):
        slot = _at(today, spec)
        return slot if slot <= now else _at(today - timedelta(days=1), spec)
    if spec.kind in ("weekly", "every_n_weeks"):
        d = today - timedelta(days=(today.weekday() - spec.weekday) % 7)
        slot = _at(d, spec)
        return slot if slot <= now else _at(d - timedelta(days=7), spec)
    y, mth = today.year, today.month
    slot = _at(_month_day(y, mth, spec.day_of_month), spec)
    if slot <= now:
        return slot
    y, mth = (y - 1, 12) if mth == 1 else (y, mth - 1)
    return _at(_month_day(y, mth, spec.day_of_month), spec)


def next_slot_after(spec: Spec, last: datetime) -> datetime:
    """The first planned moment strictly after `last`."""
    last = last.astimezone(LISBON)
    d = last.date()
    if spec.kind == "daily":
        c = _at(d, spec)
        return c if c > last else _at(d + timedelta(days=1), spec)
    if spec.kind == "every_n_days":
        return _at(d + timedelta(days=spec.every_n), spec)
    if spec.kind == "weekly":
        for i in range(0, 8):
            c = _at(d + timedelta(days=i), spec)
            if (d + timedelta(days=i)).weekday() == spec.weekday and c > last:
                return c
    if spec.kind == "every_n_weeks":
        start = d + timedelta(days=7 * spec.every_n - 6)
        return _at(start + timedelta(days=(spec.weekday - start.weekday()) % 7), spec)
    c = _at(_month_day(d.year, d.month, spec.day_of_month), spec)
    if c > last:
        return c
    y, mth = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return _at(_month_day(y, mth, spec.day_of_month), spec)


def is_due(spec: Spec, last_scheduled_start: Optional[datetime], now: datetime) -> bool:
    if not spec.enabled:
        return False
    now = now.astimezone(LISBON)
    if last_scheduled_start is None:
        return True                      # never run on schedule: run once, then keep to the plan
    last = last_scheduled_start.astimezone(LISBON)
    if spec.kind in ("daily", "weekly", "monthly"):
        return last < latest_slot(spec, now)
    return next_slot_after(spec, last) <= now


def next_run(spec: Spec, last_scheduled_start: Optional[datetime], now: datetime) -> Optional[datetime]:
    """When the job will next run (for display); `now` when it is already due; None when off."""
    if not spec.enabled:
        return None
    now = now.astimezone(LISBON)
    if is_due(spec, last_scheduled_start, now):
        return now
    return next_slot_after(spec, last_scheduled_start)


def describe(spec: Spec) -> str:
    if not spec.enabled:
        return "Off"
    t = spec.time_of_day
    n = spec.every_n
    return {
        "daily": f"Every day at {t}",
        "every_n_days": f"Every {n} days at {t}" if n != 1 else f"Every day at {t}",
        "weekly": f"Every {WEEKDAYS[spec.weekday]} at {t}",
        "every_n_weeks": (f"Every {n} weeks on {WEEKDAYS[spec.weekday]} at {t}" if n != 1
                          else f"Every {WEEKDAYS[spec.weekday]} at {t}"),
        "monthly": f"Every month on day {spec.day_of_month} at {t}",
    }[spec.kind]
