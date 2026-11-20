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
    subject_id: Optional[int] = Field(default=None, foreign_key="subjects.id")             # work-list B4
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
    needs_check: bool = Field(default=False)
    check_notes: Optional[str] = None                   # JSON list: what the check against the paper could not settle
    calendar_event_id: Optional[str] = None
    layout: Optional[str] = None                        # "sheet" | "report": how the paper is laid out
    outline_confirmed_by: Optional[str] = None          # who confirmed the outline of this paper
    outline_confirmed_at: Optional[datetime] = None
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
    topic_id: Optional[int] = Field(default=None, foreign_key="health_topics.id", index=True)
    test_id: Optional[int] = Field(default=None, foreign_key="health_tests.id", index=True)   # the known test (step 5b)
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


class HealthTopic(SQLModel, table=True):
    """The baseline: one node per heading, in a tree."""
    __tablename__ = "health_topics"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    match_key: str = Field(index=True)
    parent_id: Optional[int] = Field(default=None, foreign_key="health_topics.id", index=True)
    approved: bool = Field(default=False)
    merged_into_id: Optional[int] = Field(default=None, foreign_key="health_topics.id")
    similar_to_id: Optional[int] = Field(default=None, foreign_key="health_topics.id")   # "looks like X": a suggestion only
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HealthTest(SQLModel, table=True):
    """A known lab test ("Hemoglobina" in g/dL), whatever wording each lab prints it with (PLAN step 5b). The same
    test from two labs is one HealthTest, so it lands on one line of a person's results. A new one waits for a
    person ("Waiting for you"): approve it as new, or say it is the test it looks like."""
    __tablename__ = "health_tests"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    unit: Optional[str] = None
    unit_key: str = Field(default="", index=True)
    approved: bool = Field(default=False)
    merged_into_id: Optional[int] = Field(default=None, foreign_key="health_tests.id")
    similar_to_id: Optional[int] = Field(default=None, foreign_key="health_tests.id")    # "looks like X": a suggestion
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HealthTestAlias(SQLModel, table=True):
    """One wording a paper printed for a test, in one unit: how the next paper's wording finds its test."""
    __tablename__ = "health_test_aliases"
    __table_args__ = (UniqueConstraint("wording_key", "unit_key", name="uq_health_test_aliases_wording_unit"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    test_id: int = Field(foreign_key="health_tests.id", index=True)
    wording: str                                        # as printed, for display
    wording_key: str = Field(index=True)
    unit_key: str = Field(default="")
    source: str = "system"                              # "system" (first seen / near match) | "person" (a merge)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HealthOutlineProfile(SQLModel, table=True):
    """An outline a person confirmed as right for a provider."""
    __tablename__ = "health_outline_profiles"

    id: Optional[int] = Field(default=None, primary_key=True)
    provider_key: str = Field(index=True)
    provider: str
    layout: str
    outline_json: str                                   # JSON list of printed paths, in paper order
    confirmed_by: Optional[str] = None
    confirmed_at: datetime = Field(default_factory=datetime.utcnow)


class HealthCondition(SQLModel, table=True):
    """A long-running condition of one person (PLAN step 7a): its own record and its own subject, where its papers come
    together. Created only by a person (from a proposal or by hand); the Hub suggests which papers are about it."""
    __tablename__ = "health_conditions"
    __table_args__ = (UniqueConstraint("person_id", "match_key", name="uq_health_conditions_person_key"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="health_people.id", index=True)
    subject_id: Optional[int] = Field(default=None, foreign_key="subjects.id")      # a condition is a subject (step 6)
    name: str
    match_key: str
    started_on: Optional[date] = None
    started_note: Optional[str] = None
    status: str = Field(default="followed")            # "followed" | "stable" | "resolved"
    followed_by: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HealthEntryCondition(SQLModel, table=True):
    """Is this paper about this condition? Suggested by the Hub, confirmed or rejected by a person (never both)."""
    __tablename__ = "health_entry_conditions"

    entry_id: int = Field(foreign_key="health_entries.id", primary_key=True)
    condition_id: int = Field(foreign_key="health_conditions.id", primary_key=True)
    state: str = Field(default="suggested")            # "suggested" | "confirmed" | "rejected"
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None


class HealthFollowUp(SQLModel, table=True):
    """Something to do again by a date for one person, usually for one condition (PLAN step 7b): "repeat the MRI in
    12 months". Made by a person, or proposed from a paper's text and accepted by a person. Its reminder is a Health
    To-Do (`todo_id`); it is done when that To-Do is done."""
    __tablename__ = "health_follow_ups"

    id: Optional[int] = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="health_people.id", index=True)
    condition_id: Optional[int] = Field(default=None, foreign_key="health_conditions.id", index=True)
    what: str
    due_on: date
    source: str = Field(default="person")              # "person" | "paper"
    state: str = Field(default="active")               # "proposed" | "active" | "dismissed"
    entry_id: Optional[int] = Field(default=None, foreign_key="health_entries.id")
    source_text: Optional[str] = None                  # the words on the paper it was proposed from
    todo_id: Optional[int] = Field(default=None, foreign_key="todos.id")
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HealthVisit(SQLModel, table=True):
    """A visit to a doctor or clinic (PLAN step 7c): booked, then done with what was seen and changed."""
    __tablename__ = "health_visits"

    id: Optional[int] = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="health_people.id", index=True)
    condition_id: Optional[int] = Field(default=None, foreign_key="health_conditions.id", index=True)
    status: str = Field(default="booked")              # "booked" | "done"
    visit_on: date
    clinic: Optional[str] = None
    doctor: Optional[str] = None
    observations: Optional[str] = None
    plan_changes: Optional[str] = None
    next_appointment_on: Optional[date] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HealthNote(SQLModel, table=True):
    """A hand-made note on a condition (step 7c): a note, a reading taken at home (with its value), or an episode."""
    __tablename__ = "health_notes"

    id: Optional[int] = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="health_people.id", index=True)
    condition_id: Optional[int] = Field(default=None, foreign_key="health_conditions.id", index=True)
    kind: str = Field(default="note")                  # "note" | "reading" | "episode"
    noted_on: date
    text: str = ""
    value: Optional[float] = None
    unit: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HealthMedication(SQLModel, table=True):
    """A medicine taken for a condition (step 7c); its changes are kept, never overwritten."""
    __tablename__ = "health_medications"

    id: Optional[int] = Field(default=None, primary_key=True)
    person_id: int = Field(foreign_key="health_people.id", index=True)
    condition_id: Optional[int] = Field(default=None, foreign_key="health_conditions.id", index=True)
    name: str
    dose: Optional[str] = None
    frequency: Optional[str] = None
    started_on: Optional[date] = None
    stopped_on: Optional[date] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HealthMedicationChange(SQLModel, table=True):
    __tablename__ = "health_medication_changes"

    id: Optional[int] = Field(default=None, primary_key=True)
    medication_id: int = Field(foreign_key="health_medications.id", index=True)
    changed_on: date
    change: str                                        # "new" | "changed" | "stopped"
    dose: Optional[str] = None
    frequency: Optional[str] = None
    visit_id: Optional[int] = Field(default=None, foreign_key="health_visits.id")
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
