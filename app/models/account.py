"""Account: a real bank account or wallet that transactions belong to."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class AccountType(str, Enum):
    CHECKING = "checking"
    SAVINGS = "savings"
    CARD = "card"
    WALLET = "wallet"


class Account(SQLModel, table=True):
    __tablename__ = "accounts"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    institution: str
    currency: str = Field(default="EUR")
    account_type: AccountType = Field(default=AccountType.CHECKING)
    identifier: Optional[str] = None
    # Whose account it is (work-list B4): the person subject of its bank connection's owner.
    owner_subject_id: Optional[int] = Field(default=None, foreign_key="subjects.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
