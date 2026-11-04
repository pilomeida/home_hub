"""InboxItem: channel provenance + the classifier's suggestion + review audit
for a Document that arrived through an ingestion channel (email, Telegram).

Review state lives in Document.status (PENDING_REVIEW -> finalized, or
DISCARDED); this row never duplicates it. The suggestion is kept here, NOT
on Document.category, because "domain IS NOT NULL" (its column) means "finalized".
suggested_domain is a Category *value* string validated against the registry
when written, so registering a new domain needs no migration here."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column
from sqlmodel import Field, SQLModel
from sqlmodel.sql.sqltypes import AutoString

from app.models.strict import RefusesUnknownFields


class InboxItem(RefusesUnknownFields, SQLModel, table=True):
    __tablename__ = "inbox_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", unique=True, index=True)
    context_text: Optional[str] = None      # email subject + snippet, or Telegram caption
    external_ref: Optional[str] = None      # "email:<Message-ID>#<n>" / "telegram:<chat>:<msg>"
    suggested_domain: Optional[str] = None  # Category value, e.g. "house"
    suggested_paper_kind: Optional[str] = Field(default=None, sa_column=Column("suggested_category", AutoString))
    confidence: Optional[float] = None      # 0.0-1.0 as reported by the classifier
    classifier_note: Optional[str] = None   # classifier's one-line reason, or why it couldn't run
    received_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None


class InboxSkip(SQLModel, table=True):
    """A file that arrived through a channel and was NOT added to the Inbox, with the reason in plain words:
    an exact copy of a document the Hub already holds, or a decorative image (logo, banner, signature).
    Nothing that arrives is dropped silently: the Inbox page lists these."""
    __tablename__ = "inbox_skips"

    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str
    source: str                                       # "email" | "telegram"
    reason: str = Field(index=True)                   # "duplicate" | "decorative"
    detail: str                                       # what happened and why, for a person
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id")   # the existing document, for a duplicate
    external_ref: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
