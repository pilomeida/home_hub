"""Wiki Lint: one LintRun per pass; LintFinding rows persist across runs,
keyed by fingerprint, so a dismissed finding stays dismissed."""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.domain import Domain


class LintRunStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"   # deterministic checks done, >=1 domain's LLM audit failed
    FAILED = "failed"


class LintFindingKind(str, Enum):
    CONTRADICTION = "contradiction"
    STALE_CLAIM = "stale_claim"
    GAP = "gap"
    ORPHAN_PAGE = "orphan_page"
    MISSING_LINK = "missing_link"
    STALE_LINK = "stale_link"
    UNSOURCED_CLAIM = "unsourced_claim"
    UNINGESTED_SOURCES = "uningested_sources"
    STALE_SAVED_ANSWER = "stale_saved_answer"


class LintFindingStatus(str, Enum):
    OPEN = "open"
    DISMISSED = "dismissed"
    FIXED = "fixed"
    AUTO_RESOLVED = "auto_resolved"


class LintRun(SQLModel, table=True):
    __tablename__ = "lint_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    trigger: str                                   # "timer" | "manual"
    status: LintRunStatus = Field(default=LintRunStatus.RUNNING)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    new_count: int = Field(default=0)
    open_count: int = Field(default=0)
    auto_resolved_count: int = Field(default=0)
    errors: Optional[str] = None


class LintFinding(SQLModel, table=True):
    __tablename__ = "lint_findings"

    id: Optional[int] = Field(default=None, primary_key=True)
    fingerprint: str = Field(index=True)
    kind: LintFindingKind
    domain: Optional[Domain] = None
    summary: str
    suggested_action: Optional[str] = None
    wiki_page_ids_json: str = Field(default="[]")   # order kept (MISSING_LINK/STALE_LINK = [from, to])
    claim_ids_json: str = Field(default="[]")
    document_ids_json: str = Field(default="[]")
    record_ids_json: str = Field(default="[]")
    status: LintFindingStatus = Field(default=LintFindingStatus.OPEN)
    first_seen_run_id: int = Field(foreign_key="lint_runs.id")
    last_seen_run_id: int = Field(foreign_key="lint_runs.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
