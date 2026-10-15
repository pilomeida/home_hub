"""When is a scheduled job due? Pure functions over a JobSchedule and the time of the last
SCHEDULED run (manual runs never move the cycle). All wall-clock times are Europe/Lisbon.

A job is due when its most recent planned moment has passed without a run since. So a run missed
because the server was off happens once, at the next check — never several times over.

Every comparison of moments is done on real instants (UTC timestamps), never on Lisbon wall-clock
readings: two aware datetimes that share one tzinfo compare by their wall clock, which is wrong for the
hour that repeats when the clocks go back (25 Oct 2026) and for the hour that does not exist."""

from __future__ import annotations

import calendar
import math
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
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
    "times_per_day": "Several times a day, at fixed times",
    "every_n_minutes": "Every few minutes",
}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# The scheduler is started by a systemd timer this often (deploy/systemd/home-hub-scheduler.timer), so a
# job starts at most this late, and a schedule finer than this cannot be kept.
CHECK_INTERVAL_MINUTES = 5
MIN_MINUTES, MAX_MINUTES = 5, 1440
MAX_TIMES_PER_DAY = 24
# "Every N minutes" counts from the START of the last scheduled run, which is a few seconds after the check
# that began it. Without this allowance a 10-minute job would drift to every third check (15 minutes).
_MINUTES_SLACK = timedelta(seconds=60)


class InvalidSchedule(ValueError):
    pass


@dataclass(frozen=True)
class Spec:
    kind: str = "weekly"
    every_n: int = 1
    weekday: int = 6
    day_of_month: int = 1
    # HH:MM; for times_per_day a comma-separated list such as "07:30,13:00,18:30"; unused by every_n_minutes
    time_of_day: str = "04:30"
    enabled: bool = True


def spec_of(row: JobSchedule) -> Spec:
    return Spec(row.kind, row.every_n, row.weekday, row.day_of_month, row.time_of_day, row.enabled)


# --- times of day -------------------------------------------------------------------------------

_HHMM = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def _parse_time(text: str) -> time:
    m = _HHMM.match(text)
    if not m:
        raise ValueError(text)
    return time(int(m[1]), int(m[2]))


def times_of(spec: Spec) -> list[time]:
    """The planned times of day, in order. One for every kind but times_per_day."""
    if spec.kind == "times_per_day":
        return sorted({_parse_time(part) for part in spec.time_of_day.split(",") if part.strip()})
    return [_parse_time(spec.time_of_day)]


def _hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def _join_times(times: list[str]) -> str:
    return times[0] if len(times) == 1 else ", ".join(times[:-1]) + " and " + times[-1]


# --- how many times a day -------------------------------------------------------------------------

def runs_per_day(spec: Spec) -> int:
    """The most times this schedule can start the job within one day (any 24 hours). Used to refuse a
    schedule that would break a job's daily limit (JobDef.max_runs_per_day)."""
    if spec.kind == "times_per_day":
        return len(times_of(spec))
    if spec.kind == "every_n_minutes":
        return math.ceil(MAX_MINUTES / spec.every_n)
    return 1


def validate(spec: Spec, *, max_runs_per_day: Optional[int] = None, limit_reason: str = "",
             job_label: str = "This job") -> Spec:
    """Check a schedule and return it in its tidy form (times sorted, zero-padded, no repeats).
    Messages say why and what to do; the caller puts "Not saved." in front."""
    if spec.kind not in KINDS:
        raise InvalidSchedule("Choose how often the job should run.")
    if spec.kind == "times_per_day":
        try:
            times = times_of(spec)
        except Exception:
            times = []
        if not times or len(times) > MAX_TIMES_PER_DAY or any(
                not _HHMM.match(p) for p in spec.time_of_day.split(",") if p.strip()):
            raise InvalidSchedule(
                f"Enter between 1 and {MAX_TIMES_PER_DAY} times as HH:MM, separated by commas, for example 07:30, 13:00, 18:30. "
                "Each one is a moment of the day (Lisbon time) when the job should run.")
        spec = replace(spec, time_of_day=",".join(_hhmm(t) for t in times))
    elif spec.kind != "every_n_minutes":
        try:
            _parse_time(spec.time_of_day)
        except Exception:
            raise InvalidSchedule("Enter the time as HH:MM, for example 04:30.") from None
    if spec.kind in ("every_n_days", "every_n_weeks") and not 1 <= spec.every_n <= 365:
        raise InvalidSchedule("Enter a number between 1 and 365.")
    if spec.kind == "every_n_minutes" and not MIN_MINUTES <= spec.every_n <= MAX_MINUTES:
        raise InvalidSchedule(
            f"Enter a number of minutes between {MIN_MINUTES} and {MAX_MINUTES}. The Hub looks for jobs that are due every "
            f"{CHECK_INTERVAL_MINUTES} minutes, so a shorter gap can't be kept, and a gap of more than a day is better set as a daily schedule.")
    if spec.kind in ("weekly", "every_n_weeks") and not 0 <= spec.weekday <= 6:
        raise InvalidSchedule("Choose a day of the week.")
    if spec.kind == "monthly" and not 1 <= spec.day_of_month <= 31:
        raise InvalidSchedule("Choose a day of the month between 1 and 31.")
    if max_runs_per_day is not None and runs_per_day(spec) > max_runs_per_day:
        n = runs_per_day(spec)
        why = f"On that schedule {job_label} could run up to {n} times a day, but {limit_reason or f'it may run at most {max_runs_per_day} times a day'}"
        if spec.kind == "every_n_minutes":
            gap = math.ceil(MAX_MINUTES / max_runs_per_day)
            fix = f"Leave at least {gap} minutes ({gap // 60} hours) between runs, or choose fewer runs a day."
        else:
            fix = f"Keep at most {max_runs_per_day} times a day (for example 07:30, 13:00 and 18:30)."
        raise InvalidSchedule(f"{why}, so the extra runs would be refused. {fix}")
    return spec


# --- slots ----------------------------------------------------------------------------------------

def _on(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=LISBON)


def _at(d: date, spec: Spec) -> datetime:
    return _on(d, _parse_time(spec.time_of_day))


def _ts(moment: datetime) -> float:
    """The real instant, whatever the clock showed."""
    return moment.timestamp()


def _month_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def latest_slot(spec: Spec, now: datetime) -> datetime:
    """The most recent planned moment at or before `now` (for the calendar-based kinds)."""
    now = now.astimezone(LISBON)
    today = now.date()
    if spec.kind == "times_per_day":
        times = times_of(spec)
        for back in range(0, 3):
            d = today - timedelta(days=back)
            slots = [s for s in (_on(d, t) for t in times) if _ts(s) <= _ts(now)]
            if slots:
                return max(slots, key=_ts)
        raise InvalidSchedule("No planned time of day.")           # unreachable: a day always has a slot
    if spec.kind in ("daily", "every_n_days"):
        slot = _at(today, spec)
        return slot if _ts(slot) <= _ts(now) else _at(today - timedelta(days=1), spec)
    if spec.kind in ("weekly", "every_n_weeks"):
        d = today - timedelta(days=(today.weekday() - spec.weekday) % 7)
        slot = _at(d, spec)
        return slot if _ts(slot) <= _ts(now) else _at(d - timedelta(days=7), spec)
    y, mth = today.year, today.month
    slot = _at(_month_day(y, mth, spec.day_of_month), spec)
    if _ts(slot) <= _ts(now):
        return slot
    y, mth = (y - 1, 12) if mth == 1 else (y, mth - 1)
    return _at(_month_day(y, mth, spec.day_of_month), spec)


def next_slot_after(spec: Spec, last: datetime) -> datetime:
    """The first planned moment strictly after `last`."""
    if spec.kind == "every_n_minutes":                              # real minutes: the clock change doesn't matter
        return (last.astimezone(timezone.utc) + timedelta(minutes=spec.every_n)).astimezone(LISBON)
    last = last.astimezone(LISBON)
    d = last.date()
    if spec.kind == "times_per_day":
        times = times_of(spec)
        for ahead in range(0, 3):
            for t in times:
                c = _on(d + timedelta(days=ahead), t)
                if _ts(c) > _ts(last):
                    return c
    if spec.kind == "daily":
        c = _at(d, spec)
        return c if _ts(c) > _ts(last) else _at(d + timedelta(days=1), spec)
    if spec.kind == "every_n_days":
        return _at(d + timedelta(days=spec.every_n), spec)
    if spec.kind == "weekly":
        for i in range(0, 8):
            c = _at(d + timedelta(days=i), spec)
            if (d + timedelta(days=i)).weekday() == spec.weekday and _ts(c) > _ts(last):
                return c
    if spec.kind == "every_n_weeks":
        start = d + timedelta(days=7 * spec.every_n - 6)
        return _at(start + timedelta(days=(spec.weekday - start.weekday()) % 7), spec)
    c = _at(_month_day(d.year, d.month, spec.day_of_month), spec)
    if _ts(c) > _ts(last):
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
    if spec.kind in ("daily", "weekly", "monthly", "times_per_day"):
        return _ts(last) < _ts(latest_slot(spec, now))
    if spec.kind == "every_n_minutes":
        return _ts(last) + spec.every_n * 60 - _MINUTES_SLACK.total_seconds() <= _ts(now)
    return _ts(next_slot_after(spec, last)) <= _ts(now)


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
    if spec.kind == "times_per_day":
        return f"Every day at {_join_times([_hhmm(x) for x in times_of(spec)])}"
    if spec.kind == "every_n_minutes":
        if n % 60 == 0:
            hours = n // 60
            return "Every hour" if hours == 1 else f"Every {hours} hours"
        return f"Every {n} minutes"
    return {
        "daily": f"Every day at {t}",
        "every_n_days": f"Every {n} days at {t}" if n != 1 else f"Every day at {t}",
        "weekly": f"Every {WEEKDAYS[spec.weekday]} at {t}",
        "every_n_weeks": (f"Every {n} weeks on {WEEKDAYS[spec.weekday]} at {t}" if n != 1
                          else f"Every {WEEKDAYS[spec.weekday]} at {t}"),
        "monthly": f"Every month on day {spec.day_of_month} at {t}",
    }[spec.kind]
