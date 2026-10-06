"""CategoryNode: one node of the 3-level category tree (Group > Category >
Sub-category). Budgets (Plan 2) attach to level-3 nodes."""

from typing import Optional

from sqlmodel import Field, SQLModel


class CategoryNode(SQLModel, table=True):
    __tablename__ = "category_nodes"

    id: Optional[int] = Field(default=None, primary_key=True)
    parent_id: Optional[int] = Field(default=None, foreign_key="category_nodes.id", index=True)
    level: int
    slug: str = Field(index=True, unique=True)   # "food.groceries.supermarket"
    name: str
    kind: str                                    # "out" | "in" | "neutral"
    cadence: Optional[str] = None                # "monthly" | "yearly" | "loan" | None (no budget)
    sort_order: int = 0
