"""Budget: the planned amount for one sub-category (level-3 node) in one year.
Monthly-cadence nodes: `amount` is per month. Yearly-cadence nodes: `amount` is
for the whole year and `expected_month` is when the big bill usually lands."""

from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class Budget(SQLModel, table=True):
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("node_id", "year", name="uq_budgets_node_year"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    node_id: int = Field(foreign_key="category_nodes.id", index=True)
    year: int = Field(index=True)
    amount: float
    expected_month: Optional[int] = None
