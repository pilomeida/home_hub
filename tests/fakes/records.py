"""Real rows for tests that need something to point at, now that the test database enforces links (PLAN step 9, D7)."""
from __future__ import annotations

from decimal import Decimal

from sqlmodel import Session

from app.models.debt import Debt, DebtDirection, DebtKind


def a_loan(session: Session, number: str = "0003.17997064096") -> Debt:
    """A formal mortgage the family owes, as the loan import makes one."""
    debt = Debt(kind=DebtKind.FORMAL, direction=DebtDirection.OWED_BY_US, original_amount=100000.0,
                current_balance=Decimal("0"), name=f"Loan {number}", external_number=number, loan_type="mortgage")
    session.add(debt)
    session.commit()
    session.refresh(debt)
    return debt
