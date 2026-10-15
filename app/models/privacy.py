"""Privacy settings kept in the database, editable on the Hub (decision D12).

DocumentTypeSetting: the sensitivity tier (and redaction switch) of one
document type, identified by (domain, category). A type with no row, or a row
with tier NULL, is "not set".
PrivacySetting: small key/value switches (today: whether tiers are enforced).
TierChange: append-only log of every change."""

from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class DocumentTypeSetting(SQLModel, table=True):
    __tablename__ = "document_type_settings"
    __table_args__ = (UniqueConstraint("domain", "category", name="uq_document_type_settings_type"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    domain: str
    category: str
    tier: Optional[str] = None            # house_low|house_mid|house_high; NULL = not set
    redact: bool = Field(default=False)
    updated_by: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PrivacySetting(SQLModel, table=True):
    __tablename__ = "privacy_settings"

    key: str = Field(primary_key=True)
    value: str
    updated_by: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TierChange(SQLModel, table=True):
    __tablename__ = "tier_changes"

    id: Optional[int] = Field(default=None, primary_key=True)
    domain: Optional[str] = None          # NULL for a global switch
    category: Optional[str] = None
    what: str                             # "tier" | "redact" | "enforce"
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    changed_by: Optional[str] = None
    changed_at: datetime = Field(default_factory=datetime.utcnow)
