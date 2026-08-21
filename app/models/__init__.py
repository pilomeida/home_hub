"""Imports every model module so SQLModel.metadata is fully populated."""

from app.models.account import Account, AccountType  # noqa: F401
from app.models.person import Person  # noqa: F401
from app.models.document import Document  # noqa: F401
from app.models.transaction import Category, Transaction  # noqa: F401
from app.models.todo import Todo  # noqa: F401
from app.models.wiki import WikiChange, WikiPage  # noqa: F401
from app.models.utility_reading import UtilityReading  # noqa: F401
