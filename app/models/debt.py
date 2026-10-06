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

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import Column, Numeric
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
    current_balance: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))
    interest_rate: Optional[float] = None
    commitment_id: Optional[int] = Field(default=None, foreign_key="commitments.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # Loans & savings (all optional; formal loans read from bank documents)
    name: Optional[str] = None
    external_number: Optional[str] = Field(default=None, index=True)  # digits only
    capital_granted: Optional[float] = None
    term_months: Optional[int] = None
    start_date: Optional[date] = None
    status: str = "active"  # "active" | "closed"
    loan_type: Optional[str] = None  # "mortgage" | "personal" | "other"
    # Baseline spread over the indexante: the spread of the oldest snapshot that shows one.
    # A later different spread is a red flag, never a silent update.
    spread_percent: Optional[float] = None
