"""Merchant: the canonical identity a raw Transaction.provider string
resolves to, via rules-based normalization with an LLM fallback."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from app.models.transaction import Category, Nature


class Merchant(SQLModel, table=True):
    __tablename__ = "merchants"

    id: Optional[int] = Field(default=None, primary_key=True)
    canonical_name: str
    default_category: Category = Field(default=Category.OTHER)
    default_nature: Optional[Nature] = None
    normalized_key: str = Field(index=True, unique=True)
    confirmed: bool = Field(default=False)
    recurring_reviewed: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
