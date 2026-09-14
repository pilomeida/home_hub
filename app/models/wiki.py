"""WikiPage: current standing facts for a topic. WikiChange: its history."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.domain import Domain


class WikiPage(SQLModel, table=True):
    __tablename__ = "wiki_pages"

    id: Optional[int] = Field(default=None, primary_key=True)
    topic: str = Field(unique=True, index=True)
    facts_json: str = Field(default="{}")
    domain: Optional[Domain] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WikiChange(SQLModel, table=True):
    __tablename__ = "wiki_changes"

    id: Optional[int] = Field(default=None, primary_key=True)
    wiki_page_id: int = Field(foreign_key="wiki_pages.id", index=True)
    fact_key: str
    old_value: Optional[str] = None
    new_value: str
    document_id: Optional[int] = Field(default=None, foreign_key="documents.id")
    changed_at: datetime = Field(default_factory=datetime.utcnow)
