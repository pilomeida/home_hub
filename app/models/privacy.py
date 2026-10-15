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


class DocumentFingerprint(SQLModel, table=True):
    """A rule that recognises a known, recurring kind of document WITHOUT any AI
    (decision D13). kind: "sender" (the original sender address contains pattern),
    "words" (the words appear in the subject/caption or the PDF's own text),
    "filename" (the file name contains pattern). All matching is case-insensitive
    substring matching, on purpose: Pedro must be able to read and edit a rule."""
    __tablename__ = "document_fingerprints"

    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str
    pattern: str
    domain: str
    category: str
    note: Optional[str] = None            # where the rule came from, in plain words
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    hits: int = Field(default=0)
    last_hit_at: Optional[datetime] = None
