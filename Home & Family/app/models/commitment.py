"""Commitment: a planned recurring or yearly-cadence spend line -
Netflix (monthly), the mortgage (monthly), or IMI 2026 (yearly). Each
year of a yearly-cadence commitment is its own row."""

from datetime import date, datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.transaction import Category


class Cadence(str, Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    IRREGULAR = "irregular"


class Commitment(SQLModel, table=True):
    __tablename__ = "commitments"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    category: Category = Field(default=Category.OTHER)
    cadence: Cadence
    planned_amount: float
    year: Optional[int] = None
    next_due_date: Optional[date] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
