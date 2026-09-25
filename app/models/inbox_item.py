"""InboxItem: channel provenance + the classifier's suggestion + review audit
for a Document that arrived through an ingestion channel (email, Telegram).

Review state lives in Document.status (PENDING_REVIEW -> finalized, or
DISCARDED); this row never duplicates it. The suggestion is kept here, NOT
on Document.domain, because "domain IS NOT NULL" means "finalized".
suggested_domain is a Domain *value* string validated against the registry
when written, so registering a new domain needs no migration here."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class InboxItem(SQLModel, table=True):
    __tablename__ = "inbox_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", unique=True, index=True)
    context_text: Optional[str] = None      # email subject + snippet, or Telegram caption
    external_ref: Optional[str] = None      # "email:<Message-ID>#<n>" / "telegram:<chat>:<msg>"
    suggested_domain: Optional[str] = None  # Domain value, e.g. "house"
    suggested_category: Optional[str] = None
    confidence: Optional[float] = None      # 0.0-1.0 as reported by the classifier
    classifier_note: Optional[str] = None   # classifier's one-line reason, or why it couldn't run
    received_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
