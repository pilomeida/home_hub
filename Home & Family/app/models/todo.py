"""Todo: a simple open/done task, optionally generated from a Transaction's
due date."""

from datetime import date, datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Todo(SQLModel, table=True):
    __tablename__ = "todos"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    due_date: Optional[date] = None
    done: bool = Field(default=False)
    transaction_id: Optional[int] = Field(default=None, foreign_key="transactions.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
