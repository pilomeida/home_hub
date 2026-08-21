"""Person: a lightweight counterparty record for informal debt, so
multiple loans to/from the same person roll up together."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Person(SQLModel, table=True):
    __tablename__ = "people"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
