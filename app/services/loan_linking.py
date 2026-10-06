"""Automatic loan linking: a bank instalment debit whose text carries a
statement loan's number is linked to that Debt and filed under Loans.
Matching is by contiguous digit run only (never across separators)."""

import logging
import re
from typing import Optional

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.debt import Debt
from app.models.transaction import Transaction, TransactionType
from app.services.loan_insurance import link_insurance_transaction, load_index
from app.services.taxonomy import file_transaction, get_node

log = logging.getLogger(__name__)

MORTGAGE_SLUG = "loans-debt.loan-repayments.mortgage"
PERSONAL_SLUG = "loans-debt.loan-repayments.personal-loans"


def digits_only(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _all_loans(session: Session) -> list[Debt]:
    return list(session.exec(select(Debt).where(Debt.external_number.isnot(None))).all())


def find_loan_for(session: Session, provider_text: str, loans: Optional[list[Debt]] = None) -> Optional[Debt]:
    """The single statement loan whose number sits inside one contiguous digit
    run of the provider text; None when nothing or more than one matches.
    `loans` lets a batch caller load them once."""
    runs = re.findall(r"\d+", provider_text or "")
    if not runs:
        return None
    if loans is None:
        loans = _all_loans(session)
    matches = [d for d in loans if d.external_number and any(d.external_number in r for r in runs)]
    return matches[0] if len(matches) == 1 else None


def link_transaction_to_loan(session: Session, txn: Transaction, loans: Optional[list[Debt]] = None) -> bool:
    """Link one debit to its loan and file it under Loans. Does not commit."""
    if txn.debt_id is not None or txn.transaction_type != TransactionType.DEBIT:
        return False
    debt = find_loan_for(session, txn.provider, loans)
    if debt is None:
        return False
    slug = MORTGAGE_SLUG if debt.loan_type == "mortgage" else PERSONAL_SLUG
    txn.debt_id = debt.id
    txn.debt_candidate_reviewed = True
    file_transaction(session, txn, get_node(session, slug))
    session.add(txn)
    return True


def link_all_unlinked(session: Session) -> int:
    """Backfill: link every unlinked debit that matches a loan - instalments by
    loan number, then insurance debits (SEG...) by rule or amount. Commits.
    Loans, movements and rules are loaded once, not per debit."""
    loans = _all_loans(session)  # once, not one query per unlinked debit
    if not loans:
        return 0
    candidates = session.exec(
        select(Transaction).where(Transaction.debt_id.is_(None))
        .where(Transaction.transaction_type == TransactionType.DEBIT)
    ).all()
    count = sum(1 for t in candidates if link_transaction_to_loan(session, t, loans))
    if count:
        session.commit()
    return count + _link_insurance_debits(session, loans)


def _link_insurance_debits(session: Session, loans: list[Debt]) -> int:
    """The insurance step never undoes the instalment links: it runs after their
    commit and any failure in it is logged and rolled back on its own."""
    try:
        pending = session.exec(
            select(Transaction).where(Transaction.debt_id.is_(None))
            .where(Transaction.transaction_type == TransactionType.DEBIT)
            .where(func.lower(Transaction.provider).like("seg%"))
        ).all()
        if not pending:
            return 0
        index = load_index(session, loans)
        count = sum(1 for t in pending if link_insurance_transaction(session, t, index))
        if count:
            session.commit()
        return count
    except Exception:
        session.rollback()
        log.exception("loan insurance linking failed")
        return 0
