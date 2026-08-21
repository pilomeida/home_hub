"""Debt: formal (mortgage-style) or informal (person-to-person) debt,
one shape for both, distinguished by whether a payment schedule exists.
Formal debt links to a Commitment for its recurring payment; informal
debt links directly from Transaction.debt_id instead, since ad-hoc
loans have no fixed schedule to attach a Commitment to."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class DebtKind(str, Enum):
    FORMAL = "formal"
    INFORMAL = "informal"


class DebtDirection(str, Enum):
    OWED_TO_US = "owed_to_us"
    OWED_BY_US = "owed_by_us"


class Debt(SQLModel, table=True):
    __tablename__ = "debts"

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: DebtKind
    person_id: Optional[int] = Field(default=None, foreign_key="people.id")
    direction: Optional[DebtDirection] = None
    original_amount: float
    current_balance: float
    interest_rate: Optional[float] = None
    commitment_id: Optional[int] = Field(default=None, foreign_key="commitments.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
