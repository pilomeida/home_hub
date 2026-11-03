"""Commitment: a planned recurring or yearly-cadence spend line -
Netflix (monthly), the mortgage (monthly), or IMI 2026 (yearly). Each
year of a yearly-cadence commitment is its own row."""

from datetime import date, datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Column, String
from sqlmodel import Field, SQLModel


class Cadence(str, Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    IRREGULAR = "irregular"


class Commitment(SQLModel, table=True):
    __tablename__ = "commitments"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    # Where it sits on the spending tree, like the lines that pay it (work-list B3).
    category_id: Optional[int] = Field(default=None, foreign_key="category_nodes.id")
    # The old flat spending list (column `category`): unused, kept in the table and NOT NULL.
    unused_old_category: str = Field(default="OTHER", sa_column=Column("category", String(14), nullable=False))
    cadence: Cadence
    planned_amount: float
    year: Optional[int] = None
    next_due_date: Optional[date] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
