"""PaperType (PLAN step 12a): an area or a kind of paper Pedro approved in the Inbox, beside the ones built into the
code. One row per added area (kind NULL) and one per added kind (in a built-in or an added area). Rows are only
added, never edited, so "how many rows, and the last id" tells every process whether its list is current."""

from datetime import datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.strict import RefusesUnknownFields


class PaperType(RefusesUnknownFields, SQLModel, table=True):
    __tablename__ = "paper_types"
    __table_args__ = (UniqueConstraint("area", "kind", name="uq_paper_types_area_kind"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    area: str = Field(index=True)                      # the Category value, e.g. "work" or "financials"
    kind: Optional[str] = None                         # None: this row adds the area itself
    label: str                                         # as Pedro typed it, e.g. "Employment contract"
    description: str = ""
    created_from_document_id: Optional[int] = Field(default=None, foreign_key="documents.id")
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
