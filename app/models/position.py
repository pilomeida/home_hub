"""Loans & savings positions read from bank/loan/fund documents: per-instalment
loan movements, point-in-time snapshots, extraction bookkeeping, reminder
cycles, informal-debt auto-link rules and the informal-debt ledger.

Registered data is never discarded: `document_id` on the position rows is
nullable, withdrawing/re-filing a document only detaches them (see
position_store.detach_positions)."""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class LoanMovement(SQLModel, table=True):
    """One row per (loan, instalment number); the pieces are already summed."""
    __tablename__ = "loan_movements"
    __table_args__ = (UniqueConstraint("debt_id", "instalment_number", name="uq_loan_movements_debt_id_instalment_number"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    debt_id: int = Field(foreign_key="debts.id", index=True)
    instalment_number: int
    movement_date: date  # date of the first piece
    capital: float
    interest: float
    insurance: float = 0.0  # stored total = insurance_life + insurance_building
    insurance_life: float = 0.0
    insurance_building: float = 0.0
    balance_after: Optional[float] = None
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id", index=True)


class LoanSnapshot(SQLModel, table=True):
    __tablename__ = "loan_snapshots"
    __table_args__ = (UniqueConstraint("debt_id", "as_of", name="uq_loan_snapshots_debt_id_as_of"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    debt_id: int = Field(foreign_key="debts.id", index=True)
    as_of: date
    capital_remaining: float
    rate_percent: Optional[float] = None  # rate applied to the LAST instalment
    next_rate_percent: Optional[float] = None  # revised rate (TAN) of the NEXT instalment
    next_due_date: Optional[date] = None
    next_instalment: Optional[float] = None
    next_capital: Optional[float] = None
    next_interest: Optional[float] = None
    # Próxima Prestação block: TAN = indexante (EURIBOR) + spread; the spread should never change.
    indexante_percent: Optional[float] = None
    spread_percent: Optional[float] = None
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id", index=True)


class LoanAlert(SQLModel, table=True):
    """A red flag on a loan: interest_only | spread_changed | rate_inconsistent.
    The data it describes is always stored too; an alert stays visible until
    acknowledged (optionally with a note)."""
    __tablename__ = "loan_alerts"
    __table_args__ = (UniqueConstraint("debt_id", "kind", "ref", name="uq_loan_alerts_debt_id_kind_ref"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    debt_id: int = Field(foreign_key="debts.id", index=True)
    kind: str
    ref: str  # e.g. "inst:16" or "snap:2026-09-30"
    message: str
    detected_on: date
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id", index=True)
    acknowledged: bool = False
    ack_note: Optional[str] = None
    acknowledged_at: Optional[datetime] = None


class SavingsSnapshot(SQLModel, table=True):
    __tablename__ = "savings_snapshots"
    __table_args__ = (UniqueConstraint("account_ref", "label", "as_of", name="uq_savings_snapshots_account_ref_label_as_of"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    as_of: date
    holder: Optional[str] = None
    product: str = "fund"
    label: str
    account_ref: str
    units: Optional[float] = None
    invested: Optional[float] = None
    value: float
    periodic_amount: Optional[float] = None
    next_periodic_date: Optional[date] = None
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id", index=True)


class BalanceSnapshot(SQLModel, table=True):
    """kind: deposit | card | investments_total | loans_total"""
    __tablename__ = "balance_snapshots"
    __table_args__ = (UniqueConstraint("kind", "label", "as_of", name="uq_balance_snapshots_kind_label_as_of"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    as_of: date
    kind: str
    label: str
    amount: float
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id", index=True)


class PositionExtraction(SQLModel, table=True):
    """status: ok | failed | needs_loan. payload_json holds a parsed loan history
    awaiting a loan assignment, so assigning needs no second LLM call."""
    __tablename__ = "position_extractions"
    __table_args__ = (UniqueConstraint("document_id", name="uq_position_extractions_document_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id")
    status: str
    error: Optional[str] = None
    payload_json: Optional[str] = None
    extracted_at: datetime = Field(default_factory=datetime.utcnow)


class ReminderLog(SQLModel, table=True):
    """One row per reminder cycle."""
    __tablename__ = "reminder_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True)
    sent_at: datetime
    cycle_start: date


class DebtMatchRule(SQLModel, table=True):
    """Informal-loan auto-link rule: a normalized counterparty key -> debt."""
    __tablename__ = "debt_match_rules"

    id: Optional[int] = Field(default=None, primary_key=True)
    debt_id: int = Field(foreign_key="debts.id")
    normalized_key: str = Field(index=True, unique=True)


class LoanInsuranceRule(SQLModel, table=True):
    """Loan-insurance debit rule: a normalized provider key (e.g. "seg edf") ->
    the loan it insures and which part (life | building)."""
    __tablename__ = "loan_insurance_rules"

    id: Optional[int] = Field(default=None, primary_key=True)
    debt_id: int = Field(foreign_key="debts.id", index=True)
    component: str  # life | building
    normalized_key: str = Field(index=True, unique=True)


class DebtEntry(SQLModel, table=True):
    """One line of an informal (person-to-person) debt's ledger. kind: advance |
    repayment | adjust_up | adjust_down; amount is always positive. A line made from
    a bank transaction carries its transaction_id (unique: a transaction is at most
    one line); a manual line (cash, history before the bank data, a correction) has
    none. Balance = advances + adjust_up - repayments - adjust_down."""
    __tablename__ = "debt_entries"
    __table_args__ = (UniqueConstraint("transaction_id", name="uq_debt_entries_transaction_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    debt_id: int = Field(foreign_key="debts.id", index=True)
    kind: str
    amount: float
    entry_date: date
    transaction_id: Optional[int] = Field(default=None, foreign_key="transactions.id")
    note: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
