"""ProviderRule: the provider text of an entry decides its category (not the merchant).

For merchants that are channels (MB Way Transfer, Pag Serviços, ATM) the merchant says nothing about
what an entry is; the detail in the provider text does. `key` is provider_key(text): lowercased,
accents and number runs removed, so `TRF MBWAY P/MATIAS` rules every transfer to that person."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class ProviderRule(SQLModel, table=True):
    __tablename__ = "provider_rules"

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)
    example: str                       # one real provider text, for display
    node_id: int = Field(foreign_key="category_nodes.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
