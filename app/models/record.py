"""Record: a hand-entered source -- a domain entry typed in by a person
(a maintenance visit, later a vehicle's mileage or a doctor visit) rather
than extracted from a file. Unlike a Document it has no file of its own; it
may reference one attached Document (the receipt/report). Categories whose
entries are Records declare `kind=SourceKind.RECORD` in the registry."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.domain import Domain


class Record(SQLModel, table=True):
    __tablename__ = "records"

    id: Optional[int] = Field(default=None, primary_key=True)
    domain: Domain = Field(index=True)
    category: str = Field(index=True)
    fields_json: str = Field(default="{}")
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id", unique=True, index=True)
    entered_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    retired_at: Optional[datetime] = None
