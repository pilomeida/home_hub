"""Imports every model module so SQLModel.metadata is fully populated."""

from app.models.account import Account, AccountType  # noqa: F401
from app.models.bank import BankAccountLink, BankApiCall, BankConnection, BankConnectionStatus  # noqa: F401
from app.models.person import Person  # noqa: F401
from app.models.commitment import Cadence, Commitment  # noqa: F401
from app.models.debt import Debt, DebtDirection, DebtKind  # noqa: F401
from app.models.merchant import Merchant  # noqa: F401
from app.models.document import Document  # noqa: F401
from app.models.inbox_item import InboxItem  # noqa: F401
from app.models.transaction import Category, Nature, Transaction  # noqa: F401
from app.models.record import Record  # noqa: F401
from app.models.todo import Todo  # noqa: F401
from app.models.wiki import (  # noqa: F401
    ClaimStatus, WikiChange, WikiClaim, WikiClaimSource, WikiLink, WikiLogEntry, WikiOperation, WikiPage,
)
from app.models.utility_reading import UtilityReading  # noqa: F401
from app.models.ask import AskConversation, AskStatus, AskTurn  # noqa: F401
from app.models.wiki_lint import LintFinding, LintRun  # noqa: F401
from app.models.category_node import CategoryNode  # noqa: F401
from app.models.budget import Budget  # noqa: F401
from app.models.position import (  # noqa: F401
    BalanceSnapshot, DebtEntry, DebtMatchRule, LoanAlert, LoanMovement, LoanSnapshot, PositionExtraction, ReminderLog, SavingsSnapshot,
)
from app.models.tag import Tag, NodeTag  # noqa: F401
