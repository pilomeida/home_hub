"""The Money paper kinds (stored in Document.category). The old documents.doc_type column, which migration
3b7e9c1d2f40 copied into category, stays in the table unused."""

from enum import Enum


class FinancialsCategory(str, Enum):
    BILL = "bill"
    STATEMENT = "statement"
    POSITIONS = "statement_positions"
    LOAN_HISTORY = "loan_history"
