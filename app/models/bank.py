"""Bank connection tables: authorization state, per-account links and the
API-call ledger that keeps unattended fetches inside the bank's daily budget."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class BankConnectionStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    FAILED = "failed"


class BankConnection(SQLModel, table=True):
    __tablename__ = "bank_connections"

    id: Optional[int] = Field(default=None, primary_key=True)
    bank_name: str
    country: str
    status: BankConnectionStatus = Field(default=BankConnectionStatus.PENDING)
    state: str = Field(unique=True, index=True)
    session_id: Optional[str] = None
    valid_until: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    authorized_at: Optional[datetime] = None


class BankAccountLink(SQLModel, table=True):
    __tablename__ = "bank_account_links"

    id: Optional[int] = Field(default=None, primary_key=True)
    connection_id: int = Field(foreign_key="bank_connections.id", index=True)
    bank_account_uid: str
    iban: Optional[str] = None
    display_name: Optional[str] = None
    account_id: Optional[int] = Field(default=None, foreign_key="accounts.id")
    last_synced_at: Optional[datetime] = None
    last_error: Optional[str] = None


class BankApiCall(SQLModel, table=True):
    __tablename__ = "bank_api_calls"

    id: Optional[int] = Field(default=None, primary_key=True)
    link_id: int = Field(foreign_key="bank_account_links.id", index=True)
    called_at: datetime = Field(default_factory=datetime.utcnow)
    kind: str
