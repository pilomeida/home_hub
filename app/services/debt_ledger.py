"""The ledger of informal (person-to-person) debts: every change to a debt's
entries goes through this module. Entries come from linked bank transactions
(explicit role: advance or repayment) or are typed by hand (cash lent, history
before the bank data, corrections). After every change the debt's
current_balance (never negative) and original_amount (sum of advances) are
recomputed. Functions flush but never commit; the caller commits.

Statement loans (a Debt with an external_number) are never touched here."""

import math
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlmodel import Session, select

from app.models.category_node import CategoryNode
from app.models.debt import Debt, DebtDirection, DebtKind
from app.models.person import Person
from app.models.position import DebtEntry
from app.models.transaction import Transaction, TransactionType
from app.services.loan_math import informal_summary
from app.services.taxonomy import direction_matches, file_transaction

KINDS = ("advance", "repayment", "adjust_up", "adjust_down")
ROLES = ("advance", "repayment")  # the roles a bank transaction can take

# (direction, kind) -> node slug. A missing direction counts as owed by us.
_FILING = {
    (DebtDirection.OWED_TO_US, "advance"): "loans-debt.money-lent-out.loan-to-friends",
    (DebtDirection.OWED_TO_US, "repayment"): "loans-debt-in.repayments-received.from-friends",
    (DebtDirection.OWED_BY_US, "advance"): "loans-debt-in.money-borrowed.personal-loan-received",
    (DebtDirection.OWED_BY_US, "repayment"): "loans-debt.loan-repayments.personal-loans",
}


WITHDRAWN_NOTE = "from a withdrawn document"


def is_informal(debt: Debt) -> bool:
    return debt.external_number is None


def default_kind(direction: Optional[DebtDirection], transaction_type: TransactionType) -> Optional[str]:
    """Role of a bank transaction on a debt from the money direction; None for a
    TRANSFER (no reliable direction: the user must choose)."""
    if transaction_type == TransactionType.TRANSFER:
        return None
    lent = direction == DebtDirection.OWED_TO_US
    if transaction_type == TransactionType.DEBIT:
        return "advance" if lent else "repayment"
    return "repayment" if lent else "advance"


def transfer_default_kind(direction: Optional[DebtDirection]) -> str:
    """What a TRANSFER-type transaction is most likely, to preselect in a form."""
    return "advance" if direction == DebtDirection.OWED_TO_US else "repayment"


def derived_direction(transaction_type: TransactionType) -> Optional[DebtDirection]:
    """Direction of a NEW debt from its creating transaction; None for a TRANSFER."""
    if transaction_type == TransactionType.DEBIT:
        return DebtDirection.OWED_TO_US
    if transaction_type == TransactionType.CREDIT:
        return DebtDirection.OWED_BY_US
    return None


def _require_informal(debt: Debt) -> None:
    if not is_informal(debt):
        raise ValueError("Statement loans have no ledger")


def _valid_amount(amount) -> float:
    try:
        value = float(amount)
    except (TypeError, ValueError):
        raise ValueError("The amount must be a number")
    if not math.isfinite(value) or round(value, 2) <= 0:
        raise ValueError("The amount must be greater than zero")
    return round(value, 2)


def recompute(session: Session, debt: Debt) -> Debt:
    summary = informal_summary(session, debt)
    debt.original_amount = float(summary.advances_total)
    debt.current_balance = summary.balance
    session.add(debt)
    session.flush()
    return debt


def add_entry(session: Session, debt: Debt, kind: str, amount, entry_date: date,
              transaction: Optional[Transaction] = None, note: Optional[str] = None) -> DebtEntry:
    _require_informal(debt)
    if kind not in KINDS:
        raise ValueError(f"Unknown entry kind: {kind}")
    entry = DebtEntry(
        debt_id=debt.id, kind=kind, amount=_valid_amount(amount), entry_date=entry_date,
        transaction_id=transaction.id if transaction is not None else None,
        note=(note or "").strip() or None,
    )
    session.add(entry)
    session.flush()
    recompute(session, debt)
    return entry


def delete_manual_entry(session: Session, entry_id: int) -> None:
    entry = session.get(DebtEntry, entry_id)
    if entry is None:
        raise LookupError("Entry not found")
    if entry.transaction_id is not None:
        raise ValueError("An entry made from a bank transaction cannot be deleted")
    debt = session.get(Debt, entry.debt_id)
    session.delete(entry)
    session.flush()
    if debt is not None:
        recompute(session, debt)


def detach_entries_for_transactions(session: Session, transaction_ids) -> int:
    """Registered data is never discarded: before transactions are deleted (a
    withdrawn document, a failed statement), the ledger entries made from them
    keep living with transaction_id NULL, their kind/amount/date and a note.
    Re-linking the same transaction later re-attaches them (no double count)."""
    ids = [i for i in transaction_ids if i is not None]
    if not ids:
        return 0
    entries = session.exec(select(DebtEntry).where(DebtEntry.transaction_id.in_(ids))).all()
    for entry in entries:
        entry.transaction_id = None
        entry.note = f"{entry.note} — {WITHDRAWN_NOTE}" if entry.note else WITHDRAWN_NOTE
        session.add(entry)
    session.flush()
    return len(entries)


def _reattach_detached(session: Session, debt: Debt, kind: str, txn: Transaction) -> Optional[DebtEntry]:
    amount, when = round(abs(txn.amount), 2), _txn_date(txn)
    candidates = session.exec(
        select(DebtEntry).where(
            DebtEntry.debt_id == debt.id, DebtEntry.transaction_id.is_(None), DebtEntry.kind == kind,
            DebtEntry.amount == amount, DebtEntry.entry_date == when,
        ).order_by(DebtEntry.id)
    ).all()
    for entry in candidates:
        if entry.note and WITHDRAWN_NOTE in entry.note:
            note = entry.note.replace(f" — {WITHDRAWN_NOTE}", "").replace(WITHDRAWN_NOTE, "").strip()
            entry.note = note or None
            entry.transaction_id = txn.id
            session.add(entry)
            session.flush()
            return entry
    return None


def _file(session: Session, debt: Debt, kind: str, txn: Transaction) -> None:
    slug = _FILING.get((debt.direction or DebtDirection.OWED_BY_US, kind))
    node = session.exec(select(CategoryNode).where(CategoryNode.slug == slug)).first() if slug else None
    if node is not None and direction_matches(txn, node):
        file_transaction(session, txn, node)


def _txn_date(txn: Transaction) -> date:
    return txn.paid_date or txn.due_date or date.today()


def link_transaction_as_entry(session: Session, debt: Debt, txn: Transaction, kind: Optional[str]) -> DebtEntry:
    """Make txn a line of the debt's ledger. kind None = the default role from the
    money direction; a TRANSFER-type transaction needs an explicit kind. Sets
    txn.debt_id and marks the candidate reviewed; files the transaction under the
    matching node when its direction fits (otherwise the filing is left as is)."""
    _require_informal(debt)
    if txn.debt_id is not None and txn.debt_id != debt.id:
        raise ValueError("This transaction is already linked to another debt")
    existing = session.exec(select(DebtEntry).where(DebtEntry.transaction_id == txn.id)).first()
    if existing is not None:
        return existing
    if kind is None:
        kind = default_kind(debt.direction, txn.transaction_type)
        if kind is None:
            raise ValueError("Choose a role (advance or repayment): a transfer has no reliable direction")
    elif kind not in ROLES:
        raise ValueError("The role must be advance or repayment")
    txn.debt_id = debt.id
    txn.debt_candidate_reviewed = True
    session.add(txn)
    session.flush()
    entry = _reattach_detached(session, debt, kind, txn)
    if entry is None:
        entry = add_entry(session, debt, kind, abs(txn.amount), _txn_date(txn), transaction=txn)
    else:
        recompute(session, debt)
    _file(session, debt, kind, txn)
    return entry


def create_informal_debt(session: Session, person_name: str, direction: DebtDirection,
                         opening_amount=None, entry_date: Optional[date] = None,
                         note: Optional[str] = None) -> Debt:
    name = (person_name or "").strip()
    if not name:
        raise ValueError("Give the person's name")
    if not isinstance(direction, DebtDirection):
        raise ValueError("Choose a direction")
    person = session.exec(select(Person).where(Person.name == name)).first()
    if person is None:
        person = Person(name=name)
        session.add(person)
        session.flush()
    debt = Debt(kind=DebtKind.INFORMAL, person_id=person.id, direction=direction,
                original_amount=0.0, current_balance=Decimal("0.00"))
    session.add(debt)
    session.flush()
    if opening_amount is not None:
        add_entry(session, debt, "advance", opening_amount, entry_date or date.today(), note=note)
    return debt


def rebuild_entries_from_links(session: Session) -> int:
    """Backfill: ledger lines from the transactions already linked to informal
    debts (no external number), using the old direction rules. Idempotent (a
    transaction is at most one line). A debt with no lines yet starts from its
    creating transaction (the linked one whose amount equals original_amount, the
    lowest id among equals) or, when none does, from one manual opening line for
    the original amount with every linked transaction an ordinary entry. Never re-files
    history. Returns the number of lines created."""
    total = 0
    debts = session.exec(select(Debt).where(Debt.external_number.is_(None)).order_by(Debt.id)).all()
    for debt in debts:
        created = 0
        had_entries = session.exec(select(DebtEntry).where(DebtEntry.debt_id == debt.id)).first() is not None
        linked = session.exec(
            select(Transaction).where(Transaction.debt_id == debt.id).order_by(Transaction.id)
        ).all()
        pending = [t for t in linked
                   if session.exec(select(DebtEntry).where(DebtEntry.transaction_id == t.id)).first() is None]
        creating = None
        if not had_entries and pending:
            equal = [t for t in pending if round(abs(t.amount), 2) == round(debt.original_amount, 2)]
            creating = equal[0] if equal else None  # lowest id among equals (pending is id-ordered)
        if not had_entries and creating is None and debt.original_amount > 0:
            opened = debt.created_at.date() if debt.created_at else date.today()
            add_entry(session, debt, "advance", debt.original_amount, opened, note="opening balance (migrated)")
            created += 1
        for txn in pending:
            if txn is creating:
                kind = "advance"  # the transaction the debt was created from is its first advance
            else:
                kind = default_kind(debt.direction, txn.transaction_type) or transfer_default_kind(debt.direction)
            add_entry(session, debt, kind, abs(txn.amount), _txn_date(txn), transaction=txn)
            created += 1
        if created or had_entries:
            recompute(session, debt)
        total += created
    return total
