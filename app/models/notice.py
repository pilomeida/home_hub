"""Notices: what the Hub has told a person on their phone, and whether it still holds (finding F15, work-list C6).

One row per (kind, ref): a thing that needs a person, found by app/services/notices.py. It is sent once when it is
first found, reminded at most once while it stays open (only for the kinds that remind), and closed when it no
longer holds. The four kinds that reach the phone were chosen by Pedro on 2026-11-03."""

from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class Notice(SQLModel, table=True):
    __tablename__ = "notices"
    __table_args__ = (UniqueConstraint("kind", "ref", name="uq_notices_kind_ref"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str                                   # bank_expired | job_failing | bill_due | loan_flag
    ref: str                                    # what it is about: "connection:9", "job:backup", "todo:12", "loan-alert:4"
    text: str                                   # the line sent (administrative facts only, decision D21)
    link: Optional[str] = None                  # where in the Hub to act on it
    first_seen: datetime = Field(default_factory=datetime.utcnow)
    sent_at: Optional[datetime] = None          # first delivery; None = waiting to be sent
    reminded_at: Optional[datetime] = None      # the one reminder, for the kinds that remind
    resolved_at: Optional[datetime] = None      # no longer holds
