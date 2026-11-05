"""Subject (work-list B4): who or what a paper or a line is about, once: a person, an account, a counterparty, a
thing, a policy, a condition. The other stores (Loans' people, Health's people, bank owners, accounts, payees that
are people) point at their subject. Built on Health's people (find-or-create, confirm, merge with alias): a subject
merged into another stays as an alias, so a spelling still leads to the subject kept.

The Hub proposes that two subjects (or a subject and a payee) are the same; a person says yes or no, once
(Pedro, 2026-11-05). Nothing is joined without a yes."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.strict import RefusesUnknownFields


class SubjectKind(str, Enum):
    PERSON = "person"
    ACCOUNT = "account"
    COUNTERPARTY = "counterparty"
    THING = "thing"
    POLICY = "policy"
    CONDITION = "condition"


class Subject(RefusesUnknownFields, SQLModel, table=True):
    __tablename__ = "subjects"

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: SubjectKind
    name: str
    match_key: str = Field(index=True)              # app/services/names.py name_key
    confirmed: bool = Field(default=False)
    merged_into_id: Optional[int] = Field(default=None, foreign_key="subjects.id")   # an alias, not a subject
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SubjectMatch(RefusesUnknownFields, SQLModel, table=True):
    """A proposal: is `subject` the same as `other_subject` (or as the payee `merchant`)? decision: None while
    waiting, "same" or "different" once a person answered."""
    __tablename__ = "subject_matches"
    __table_args__ = (UniqueConstraint("subject_id", "other_subject_id", "merchant_id", name="uq_subject_matches_pair"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    subject_id: int = Field(foreign_key="subjects.id", index=True)
    other_subject_id: Optional[int] = Field(default=None, foreign_key="subjects.id")
    merchant_id: Optional[int] = Field(default=None, foreign_key="merchants.id")
    reason: str
    decision: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
