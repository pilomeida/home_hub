"""Health's own tables. A paper itself is a Document (domain "health"); these hold what was read from it
and what the Health tab filters on: whose it is, what it is about, and every measurement."""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class HealthPerson(SQLModel, table=True):
    __tablename__ = "health_people"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    match_key: str = Field(index=True)
    confirmed: bool = Field(default=False)
    merged_into_id: Optional[int] = Field(default=None, foreign_key="health_people.id")   # an alias, not a person
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HealthArea(SQLModel, table=True):
    """A body area/system ("Eyes") or a condition ("Stroke"). Proposed by the AI, approved by a person."""
    __tablename__ = "health_areas"
    __table_args__ = (UniqueConstraint("kind", "match_key", name="uq_health_areas_kind_key"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    kind: str = Field(index=True)                       # "area" | "condition"
    match_key: str
    approved: bool = Field(default=False)
    merged_into_id: Optional[int] = Field(default=None, foreign_key="health_areas.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HealthEntry(SQLModel, table=True):
    """What the Health tab lists: one row per filed health paper."""
    __tablename__ = "health_entries"

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", unique=True, index=True)
    person_id: Optional[int] = Field(default=None, foreign_key="health_people.id", index=True)
    entry_date: Optional[date] = None
    year: Optional[int] = Field(default=None, index=True)
    kind: Optional[str] = Field(default=None, index=True)
    provider: Optional[str] = None
    summary: Optional[str] = None
    full_text: Optional[str] = None
    amount_billed: Optional[float] = None
    amount_reimbursed: Optional[float] = None
    amount_owed: Optional[float] = None
    needs_check: bool = Field(default=False)
    check_notes: Optional[str] = None                   # JSON list: what the check against the paper could not settle
    calendar_event_id: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class HealthEntryTag(SQLModel, table=True):
    __tablename__ = "health_entry_tags"

    entry_id: int = Field(foreign_key="health_entries.id", primary_key=True)
    area_id: int = Field(foreign_key="health_areas.id", primary_key=True)


class HealthMeasurement(SQLModel, table=True):
    __tablename__ = "health_measurements"

    id: Optional[int] = Field(default=None, primary_key=True)
    entry_id: int = Field(foreign_key="health_entries.id", index=True)
    person_id: Optional[int] = Field(default=None, foreign_key="health_people.id", index=True)
    measured_on: Optional[date] = None
    test: str
    test_key: str = Field(default="", index=True)
    value: Optional[float] = None
    value_text: Optional[str] = None
    unit: Optional[str] = None
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None
    out_of_range: Optional[bool] = None
    needs_check: bool = Field(default=False)
    source_text: Optional[str] = None                   # the line as printed, shown beside the value
    # Where the result sits on the paper (Pedro, 2026-10-16): the headings above it, outermost first, joined with " › "
    # (e.g. "Estudo da urina › Urina tipo II › Caracteres gerais"), its order on the paper, and everything printed
    # beside it as reference (interval, limits, risk levels), as printed.
    group_path: str = Field(default="")
    position: int = Field(default=0)
    reference_text: Optional[str] = None
    method: Optional[str] = None                        # '(Mét.Jaffé cinético)' on the paper: data, but not part of the test's name
    is_previous: bool = Field(default=False)            # printed on this paper as an EARLIER result of the same test
