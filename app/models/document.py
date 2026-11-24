"""Document: a source file (uploaded, forwarded, or synced)."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Column, Enum as SAEnum
from sqlmodel import Field, SQLModel
from sqlmodel.sql.sqltypes import AutoString

from app.models.category import Category, CategoryType
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
    # it waits in the Inbox or after it was discarded. (The old `doc_type` column was dropped 2026-11-20, PLAN step 9
    # D1; migration 3b7e9c1d2f40 had copied it here.)
    paper_kind: Optional[str] = Field(default=None, sa_column=Column("category", AutoString, index=True))
    fields_json: str = Field(default="{}")
    # The fields a person typed or changed (JSON list of keys): "Read again" keeps them (PLAN step 5d).
    person_fields_json: str = Field(default="[]")
    category: Optional[Category] = Field(default=None, sa_column=Column("domain", CategoryType()))   # the model's category (column still `domain`)
    password_protected: bool = Field(default=False)
    failure_reason: Optional[str] = None
    uploaded_by: Optional[str] = None
    # house_low|house_mid|house_high (app.privacy.tiers); NULL = no tier wired for this type yet.
    sensitivity_tier: Optional[str] = None
    account_id: Optional[int] = Field(default=None, foreign_key="accounts.id")
    # PLAN step 12d: `filename` is the name the paper goes by once filed (`date · kind · what it is · who or item`,
    # app/services/paper_names.py); the name it came in with stays here, as where it came from. NULL until renamed.
    original_filename: Optional[str] = None
    tags_json: str = Field(default="[]")                                   # free tags, as approved (JSON list)
    who_subject_id: Optional[int] = Field(default=None, foreign_key="subjects.id", index=True)   # who it is about
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PaperItem(RefusesUnknownFields, SQLModel, table=True):
    """One physical thing a filed paper lists (PLAN step 12d): an invoice of three appliances has three rows, each
    its own card in House. The thing is a subject (S5); its room is a room of the room list."""
    __tablename__ = "paper_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="documents.id", index=True)
    name: str
    room: Optional[str] = None
    warranty_end: Optional[str] = None                                     # yyyy-mm-dd
    subject_id: Optional[int] = Field(default=None, foreign_key="subjects.id", index=True)
