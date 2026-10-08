"""Transaction: an extracted line item from a Document, plus the controlled
category vocabulary."""

from datetime import date, datetime
from enum import Enum
from typing import Optional

from sqlalchemy import UniqueConstraint
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


class Nature(str, Enum):
    ESSENTIAL = "essential"
    DISCRETIONARY = "discretionary"


class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("account_id", "external_id", name="uq_transactions_account_external"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", index=True)
    provider: str
    category: Category = Field(default=Category.OTHER)
    category_id: Optional[int] = Field(default=None, foreign_key="category_nodes.id", index=True)
    transaction_type: TransactionType = Field(
        default=TransactionType.DEBIT, sa_column_kwargs={"server_default": "DEBIT"}
    )
    account_id: Optional[int] = Field(default=None, foreign_key="accounts.id")
    amount: float
    currency: str = Field(default="EUR")
    due_date: Optional[date] = None
    paid_date: Optional[date] = None
    statement_period: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    commitment_id: Optional[int] = Field(default=None, foreign_key="commitments.id")
    debt_id: Optional[int] = Field(default=None, foreign_key="debts.id")
    nature: Optional[Nature] = None
    merchant_id: Optional[int] = Field(default=None, foreign_key="merchants.id")
    debt_candidate_reviewed: Optional[bool] = None
    linked_transaction_id: Optional[int] = Field(default=None, foreign_key="transactions.id")
    # A row made from a bill, invoice or receipt is the DOCUMENT's record of a payment. Once the bank
    # statements show that payment, this points at the bank row, which is the transaction; a settled row
    # counts in no total (see services/document_reconcile.py).
    settled_by_id: Optional[int] = Field(default=None, foreign_key="transactions.id", index=True)
    external_id: Optional[str] = Field(default=None, index=True)
