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
    default_category_id: Optional[int] = Field(default=None, foreign_key="category_nodes.id")
    default_nature: Optional[Nature] = None
    normalized_key: str = Field(index=True, unique=True)
    confirmed: bool = Field(default=False)
    # A channel (MB Way Transfer, Pag Serviços, ATM): the merchant says nothing about what an entry is,
    # so its default category is not applied to new entries and bulk merging skips it.
    # Merged into another merchant: kept as an alias so its bank text still resolves to the survivor.
    merged_into_id: Optional[int] = Field(default=None, foreign_key="merchants.id", index=True)
    by_provider: bool = Field(default=False, sa_column_kwargs={"server_default": "0"})
    recurring_reviewed: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
