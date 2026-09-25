"""The knowledge layer (Karpathy "LLM Wiki" pattern, adapted):

- raw sources  = Document rows + stored files (never modified by the wiki);
- wiki         = WikiPage rows (entity pages such as a House item, or
                 free-form topic pages), each made of WikiClaims; every claim
                 links back to the Document(s) asserting it (WikiClaimSource);
                 a changed claim SUPERSEDES the old one, never deletes it;
- log          = WikiLogEntry, append-only record of every operation
                 (ingest / edit / query / lint);
- index        = generated from WikiPage (app.services.wiki_store.build_wiki_index);
- schema       = per-domain WikiSchema declared in the domain registry.

WikiPage.facts_json is a cache of the page's ACTIVE claims, rebuilt only by
app.services.wiki_store. WikiChange is the pre-claims history table: kept
read-only, no longer written."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.domain import Domain

TOPIC_PAGE_TYPE = "topic"
ANSWER_PAGE_TYPE = "answer"  # Plan C's saved Ask answers (no source document)


class WikiPage(SQLModel, table=True):
    __tablename__ = "wiki_pages"
    __table_args__ = (UniqueConstraint("page_type", "entity_key", name="uq_wiki_pages_page_type_entity_key"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    topic: str = Field(unique=True, index=True)
    facts_json: str = Field(default="{}")
    domain: Optional[Domain] = None
    page_type: str = Field(default=TOPIC_PAGE_TYPE, index=True)
    entity_key: Optional[str] = None
    summary: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WikiChange(SQLModel, table=True):
    """Legacy per-fact history (pre-claims). Read-only; not written anymore."""

    __tablename__ = "wiki_changes"

    id: Optional[int] = Field(default=None, primary_key=True)
    wiki_page_id: int = Field(foreign_key="wiki_pages.id", index=True)
    fact_key: str
    old_value: Optional[str] = None
    new_value: str
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id")
    changed_at: datetime = Field(default_factory=datetime.utcnow)


class ClaimStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class WikiClaim(SQLModel, table=True):
    __tablename__ = "wiki_claims"

    id: Optional[int] = Field(default=None, primary_key=True)
    page_id: int = Field(foreign_key="wiki_pages.id", index=True)
    key: str
    label: Optional[str] = None
    value: str
    note: Optional[str] = None
    status: ClaimStatus = Field(default=ClaimStatus.ACTIVE)
    superseded_by_claim_id: Optional[int] = Field(default=None, foreign_key="wiki_claims.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    superseded_at: Optional[datetime] = None


class WikiClaimSource(SQLModel, table=True):
    """Links a claim to the source asserting it: a Document OR a Record
    (exactly one). `withdrawn_at` is set when the source was re-filed or
    edited and no longer asserts the claim while other sources still do."""

    __tablename__ = "wiki_claim_sources"
    __table_args__ = (
        UniqueConstraint("claim_id", "document_id", name="uq_wiki_claim_sources_claim_document"),
        UniqueConstraint("claim_id", "record_id", name="uq_wiki_claim_sources_claim_record"),
        CheckConstraint("(document_id IS NULL) <> (record_id IS NULL)", name="ck_wiki_claim_sources_one_source"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    claim_id: int = Field(foreign_key="wiki_claims.id", index=True)
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id", index=True)
    record_id: Optional[int] = Field(default=None, foreign_key="records.id", index=True)
    withdrawn_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WikiOperation(str, Enum):
    INGEST = "ingest"
    EDIT = "edit"
    QUERY = "query"          # Plan C
    LINT = "lint"            # Plan C
    REVIEW = "review"        # Plan B's Inbox approve/discard
    MIGRATION = "migration"


class WikiLogEntry(SQLModel, table=True):
    __tablename__ = "wiki_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    occurred_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    operation: WikiOperation
    description: str
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id")
    record_id: Optional[int] = Field(default=None, foreign_key="records.id")
    page_ids_json: str = Field(default="[]")


class WikiLink(SQLModel, table=True):
    """A directed cross-link between two wiki pages (a bidirectional relation
    is two rows). Written by wiki_store.add_link / apply_claims(links=...)."""

    __tablename__ = "wiki_links"
    __table_args__ = (UniqueConstraint("from_page_id", "to_page_id", name="uq_wiki_links_from_to"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    from_page_id: int = Field(foreign_key="wiki_pages.id", index=True)
    to_page_id: int = Field(foreign_key="wiki_pages.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)