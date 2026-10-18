from datetime import date
from typing import Optional

from sqlmodel import Field, SQLModel


class BaselinePosition(SQLModel, table=True):
    """What the family had on the baseline day (app/baseline.py): one row per bank account balance, loan
    capital or fund holding. `kind` is "cash" | "loan" | "fund"; cash rows carry account_id, loan rows debt_id."""
    __tablename__ = "baseline_positions"

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(index=True)
    label: str
    amount: float
    as_of: date
    account_id: Optional[int] = Field(default=None, foreign_key="accounts.id")
    debt_id: Optional[int] = Field(default=None, foreign_key="debts.id")
    source: Optional[str] = None      # where the figure comes from, in plain words
