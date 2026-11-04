"""Document: a source file (uploaded, forwarded, or synced)."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Column, Enum as SAEnum
from sqlmodel import Field, SQLModel
from sqlmodel.sql.sqltypes import AutoString

from app.models.category import Category
from app.models.strict import RefusesUnknownFields


class DocumentSource(str, Enum):
    MANUAL = "manual"
    EMAIL = "email"
    API = "api"
    TELEGRAM = "telegram"


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    NEEDS_ATTENTION = "needs_attention"
    # Channel intake (email/Telegram): waiting in the Inbox for a human to
    # approve a domain + category. Always unfinalized (domain = NULL).
    PENDING_REVIEW = "pending_review"
    # Rejected in the Inbox. Row and file are kept (raw sources are immutable).
    DISCARDED = "discarded"


class Document(RefusesUnknownFields, SQLModel, table=True):
    __tablename__ = "documents"

    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str
    file_path: str
    content_hash: str = Field(index=True)
    source: DocumentSource
    status: DocumentStatus = Field(default=DocumentStatus.PENDING)
    # The paper KIND (glossary, docs/ARCHITECTURE.md). Set on every filed document (work-list B6); empty only while
    # it waits in the Inbox or after it was discarded. The old `doc_type` column stays in the table, unused
    # (additive-only migrations; migration 3b7e9c1d2f40 copied it here).
    paper_kind: Optional[str] = Field(default=None, sa_column=Column("category", AutoString, index=True))
    fields_json: str = Field(default="{}")
    category: Optional[Category] = Field(default=None, sa_column=Column("domain", SAEnum(Category)))   # the model's category (column still `domain`)
    password_protected: bool = Field(default=False)
    failure_reason: Optional[str] = None
    uploaded_by: Optional[str] = None
    # house_low|house_mid|house_high (app.privacy.tiers); NULL = no tier wired for this type yet.
    sensitivity_tier: Optional[str] = None
    account_id: Optional[int] = Field(default=None, foreign_key="accounts.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
