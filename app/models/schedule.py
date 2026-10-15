"""Background-job schedules, editable on the Hub (decision D15)."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class JobSchedule(SQLModel, table=True):
    """How often one background job runs. kind: daily | every_n_days | weekly |
    every_n_weeks | monthly | times_per_day | every_n_minutes. A job with no row uses its built-in default.
    time_of_day is one HH:MM, or for times_per_day a comma-separated list ("07:30,13:00,18:30");
    every_n_minutes uses every_n (5-1440) and ignores time_of_day."""
    __tablename__ = "job_schedules"

    job: str = Field(primary_key=True)
    kind: str
    every_n: int = Field(default=1)             # the N of every_n_days / every_n_weeks / every_n_minutes
    weekday: int = Field(default=0)             # 0 = Monday … 6 = Sunday (weekly, every_n_weeks)
    day_of_month: int = Field(default=1)        # 1–31, clamped to the month's length (monthly)
    time_of_day: str = Field(default="04:30")   # HH:MM, Europe/Lisbon
    enabled: bool = Field(default=True)
    updated_by: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class JobRun(SQLModel, table=True):
    """One run of a scheduled job (by the clock or by hand)."""
    __tablename__ = "job_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    job: str = Field(index=True)
    trigger: str                                # "schedule" | "manual"
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    outcome: str = Field(default="running")     # running | ok | partial | failed | skipped
    summary: Optional[str] = None               # plain-language result or reason


class ProblemEntry(SQLModel, table=True):
    """Something the Hub was asked to do and could not (decision D14). Kept so a failure that
    happened in the background, or in a Telegram reply, is still visible later with its reason."""
    __tablename__ = "problem_entries"

    id: Optional[int] = Field(default=None, primary_key=True)
    at: datetime = Field(default_factory=datetime.utcnow, index=True)
    area: str                                   # Uploads | Inbox | Reading documents | Telegram | Email | Scheduled jobs …
    what: str                                   # the fact: "Not uploaded."
    why: str                                    # the reason
    next_step: str                              # what the person can do
    link: Optional[str] = None                  # where to go to act on it
    technical: Optional[str] = None             # the raw detail, for whoever has to dig
    ref: Optional[str] = Field(default=None, index=True)   # "document:12", "job:wiki_lint" …
    times: int = Field(default=1)
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
