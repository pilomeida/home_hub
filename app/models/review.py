"""ReviewItem: one thing waiting for a person, on the one "Waiting for you" queue (PLAN step 3, work-list C1).

Each item says what is waiting (`title`), why (`why`), where the evidence is (`evidence_url`), and the answers a
person can give (`options_json`: [{"value", "label"}]). A decision stamps who and when; "remember this answer"
writes a step-2 rule (app/services/rules.py). Statuses: waiting, approved, discarded, snoozed (back after
`snoozed_until`). `kind` + `ref` name the thing (e.g. "ai_rule" / "rule:12"); there is at most one open item for it."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.strict import RefusesUnknownFields

WAITING, APPROVED, DISCARDED, SNOOZED = "waiting", "approved", "discarded", "snoozed"
OPEN = (WAITING, SNOOZED)


class ReviewItem(RefusesUnknownFields, SQLModel, table=True):
    __tablename__ = "review_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    area: str = Field(index=True)                  # money | health | inbox | house | ...
    kind: str = Field(index=True)                  # which producer made it, and which handler answers it
    ref: str = Field(index=True)
    title: str
    why: str
    evidence_url: Optional[str] = None
    options_json: str = "[]"
    status: str = Field(default=WAITING, index=True)
    snoozed_until: Optional[datetime] = None
    answer: Optional[str] = None
    remembered: bool = False
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
