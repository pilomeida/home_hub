"""Imports every model module so SQLModel.metadata is fully populated."""

from app.models.document import Document  # noqa: F401
from app.models.transaction import Category, Transaction  # noqa: F401
from app.models.todo import Todo  # noqa: F401
