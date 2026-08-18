"""Transaction: an extracted line item from a Document, plus the controlled
category vocabulary."""

from datetime import date, datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class Category(str, Enum):
    ELECTRICITY = "electricity"
    WATER = "water"
    GAS = "gas"
    TELECOM = "telecom"
    INSURANCE = "insurance"
    SUBSCRIPTIONS = "subscriptions"
    GROCERIES = "groceries"
    HEALTH = "health"
    HOME = "home"
    INCOME = "income"
    TRANSFER = "transfer"
    ATM_WITHDRAWAL = "atm_withdrawal"
    RESTAURANTS = "restaurants"
    SHOPPING = "shopping"
    OTHER_EXPENSE = "other_expense"
    OTHER = "other"


class TransactionType(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"
    TRANSFER = "transfer"


class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", index=True)
    provider: str
    category: Category = Field(default=Category.OTHER)
    transaction_type: TransactionType = Field(default=TransactionType.DEBIT)
    amount: float
    currency: str = Field(default="EUR")
    due_date: Optional[date] = None
    paid_date: Optional[date] = None
    statement_period: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
