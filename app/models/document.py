"""Document: a source file (uploaded, forwarded, or synced)."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.domain import Domain


class DocumentSource(str, Enum):
    MANUAL = "manual"
    EMAIL = "email"
    API = "api"


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    NEEDS_ATTENTION = "needs_attention"


class Document(SQLModel, table=True):
    __tablename__ = "documents"

    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str
    file_path: str
    content_hash: str = Field(index=True)
    source: DocumentSource
    status: DocumentStatus = Field(default=DocumentStatus.PENDING)
    doc_type: Optional[str] = None
    domain: Optional[Domain] = None
    password_protected: bool = Field(default=False)
    failure_reason: Optional[str] = None
    uploaded_by: Optional[str] = None
    account_id: Optional[int] = Field(default=None, foreign_key="accounts.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
