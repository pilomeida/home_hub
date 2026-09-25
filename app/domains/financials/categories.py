"""Financials document categories (stored in Document.category). Formerly
Document.doc_type, which migration 3b7e9c1d2f40 copied into category."""

from enum import Enum


class FinancialsCategory(str, Enum):
    BILL = "bill"
    STATEMENT = "statement"