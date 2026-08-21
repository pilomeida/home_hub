"""Debt: formal (mortgage-style) or informal (person-to-person) debt,
one shape for both, distinguished by whether a payment schedule exists.
Formal debt with a recurring schedule links via commitment_id to an
evergreen (MONTHLY/QUARTERLY) Commitment for its expected payment --
a yearly-cadence formal debt's schedule isn't representable this way
today, since each year of a yearly Commitment is its own row. Any debt
(formal or informal) may also be linked directly from a transaction via
Transaction.debt_id, a general, unrestricted mechanism -- not limited
to informal debt -- used for ad-hoc draws/repayments with no schedule
to attach a Commitment to."""

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
