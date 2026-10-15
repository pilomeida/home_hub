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
    description: str
    category: Category = Field(default=Category.OTHER)
    category_id: Optional[int] = Field(default=None, foreign_key="category_nodes.id", index=True)
    transaction_type: TransactionType = Field(
        default=TransactionType.DEBIT, sa_column_kwargs={"server_default": "DEBIT"}
    )
    account_id: Optional[int] = Field(default=None, foreign_key="accounts.id")
    amount: float
    currency: str = Field(default="EUR")
    due_date: Optional[date] = None
    issue_date: Optional[date] = None  # bills: the date the document was issued (payment may be made from then on)
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
    # --- checking an extracted line (decision D17) ---------------------------------------------------
    # NULL review_status = nothing to check: the row counts. "needs_check" = held out of every total and
    # out of Ask until a person decides; "confirmed" = a person said it is right (counts); "rejected" =
    # a person said it is not a real line (counts nowhere). Use Transaction.counts() in queries, never
    # a hand-written filter.
    source_page: Optional[int] = None            # page of the source document the line was read from
    review_status: Optional[str] = Field(default=None, index=True)
    review_reason: Optional[str] = None          # plain words: why it needs checking
    review_original_json: Optional[str] = None   # what was first read, kept when a person corrects it
    checked_by: Optional[str] = None
    checked_at: Optional[datetime] = None

    @classmethod
    def counts(cls):
        """SQL condition: this row counts in totals, budgets, Overview and Ask. Excludes rows a bill
        already settled and rows waiting for, or failed, a person's check."""
        from sqlalchemy import and_, or_
        return and_(cls.settled_by_id.is_(None),
                    or_(cls.review_status.is_(None), cls.review_status == "confirmed"))


class StatementCheck(SQLModel, table=True):
    """Does a bank statement add up? opening + all lines should equal closing (decision D17).
    status: "ok" | "mismatch" | "unverified" (the statement prints no balances) | "accepted"
    (a person looked at a mismatch and said it is fine)."""
    __tablename__ = "statement_checks"

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", unique=True, index=True)
    opening_balance: Optional[float] = None
    closing_balance: Optional[float] = None
    lines_total: Optional[float] = None          # signed sum of the lines read
    difference: Optional[float] = None           # closing - (opening + lines_total)
    status: str = Field(default="unverified")
    note: Optional[str] = None                   # plain words: what the check found
    checked_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
