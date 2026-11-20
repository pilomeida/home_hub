"""The Transactions page's list: the filters it offers, one page of rows, the count, and the names and labels each
row shows (merchant, account, transfer counterpart, category path, flow, the papers a bank row settled); and the
changes a person makes from it (a bulk edit, a merchant's rows tied to a new commitment)."""

from typing import Optional

from sqlmodel import Session, select

from app.models.account import Account
from app.models.category_node import CategoryNode
from app.models.commitment import Cadence, Commitment
from app.models.document import Document
from app.models.merchant import Merchant
from app.models.transaction import Nature, Transaction, TransactionType
from app.services.taxonomy import descendant_ids, file_transaction, flow_of, is_refund

PAGE_SIZE = 100


def apply_transaction_filters(
    session: Session,
    statement,
    nature: Optional[str] = None,
    account_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    commitment_id: Optional[int] = None,
    debt_id: Optional[int] = None,
    transaction_type: Optional[str] = None,
    transaction_id: Optional[int] = None,
    merchant_id: Optional[int] = None,
    category_node: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
):
    if q:
        like = f"%{q}%"
        statement = statement.where(
            Transaction.description.ilike(like)
            | Transaction.merchant_id.in_(select(Merchant.id).where(Merchant.canonical_name.ilike(like))))
    if tag:
        from app.services.tag_service import tag_node_ids
        node_ids = tag_node_ids(session, tag)
        statement = statement.where(Transaction.category_id.in_(node_ids if node_ids else [-1]))
    if category_node:
        node = session.exec(select(CategoryNode).where(CategoryNode.slug == category_node)).first()
        statement = statement.where(
            Transaction.category_id.in_(descendant_ids(session, node.id) if node else [])
        )
    if nature:
        statement = statement.where(Transaction.nature == Nature(nature))
    if account_id:
        statement = statement.where(Transaction.account_id == account_id)
    if date_from:
        statement = statement.where(Transaction.paid_date >= date_from)
    if date_to:
        statement = statement.where(Transaction.paid_date <= date_to)
    if commitment_id:
        statement = statement.where(Transaction.commitment_id == commitment_id)
    if debt_id:
        statement = statement.where(Transaction.debt_id == debt_id)
    if transaction_type:
        statement = statement.where(Transaction.transaction_type == TransactionType(transaction_type))
    if transaction_id:
        statement = statement.where(Transaction.id == transaction_id)
    if merchant_id:
        statement = statement.where(Transaction.merchant_id == merchant_id)
    return statement

def filtered_transactions(
    session: Session,
    nature: Optional[str] = None,
    account_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    commitment_id: Optional[int] = None,
    debt_id: Optional[int] = None,
    transaction_type: Optional[str] = None,
    page: int = 1,
    transaction_id: Optional[int] = None,
    merchant_id: Optional[int] = None,
    category_node: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
):
    # keeps held rows: the Transactions page is the full ledger: it shows every row (settled ones with a marker) and adds up nothing
    statement = (select(Transaction).where(Transaction.after_baseline())
                 .order_by(Transaction.paid_date.desc(), Transaction.id.desc()))
    statement = apply_transaction_filters(
        session, statement, nature=nature, account_id=account_id,
        date_from=date_from, date_to=date_to, commitment_id=commitment_id,
        debt_id=debt_id, transaction_type=transaction_type, transaction_id=transaction_id,
        merchant_id=merchant_id, category_node=category_node, tag=tag, q=q,
    )
    statement = statement.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)
    return session.exec(statement).all()

def lookup_dicts_for(session: Session, transactions: list[Transaction]) -> tuple[dict, dict, dict]:
    """Build {id: name}/{id: Transaction} lookups for the merchants,
    accounts, and linked (internal-transfer counterpart) transactions
    referenced by a batch of transactions, for row rendering. There is no
    SQLModel Relationship convention anywhere in this codebase's models, so
    rows are annotated via these dicts rather than introducing one here."""
    merchant_ids = {t.merchant_id for t in transactions if t.merchant_id is not None}
    linked_ids = {t.linked_transaction_id for t in transactions if t.linked_transaction_id is not None}
    linked_transactions = {
        lt.id: lt
        # keeps held rows: display lookup of a row's transfer counterpart
        for lt in (session.exec(select(Transaction).where(Transaction.id.in_(linked_ids))).all() if linked_ids else [])
    }
    account_ids = {t.account_id for t in transactions if t.account_id is not None}
    account_ids |= {lt.account_id for lt in linked_transactions.values() if lt.account_id is not None}
    merchant_names = {
        m.id: m.canonical_name
        for m in (session.exec(select(Merchant).where(Merchant.id.in_(merchant_ids))).all() if merchant_ids else [])
    }
    account_names = {
        a.id: a.name
        for a in (session.exec(select(Account).where(Account.id.in_(account_ids))).all() if account_ids else [])
    }
    return merchant_names, account_names, linked_transactions

def count_filtered_transactions(
    session: Session,
    nature: Optional[str] = None,
    account_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    commitment_id: Optional[int] = None,
    debt_id: Optional[int] = None,
    transaction_type: Optional[str] = None,
    transaction_id: Optional[int] = None,
    merchant_id: Optional[int] = None,
    category_node: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
) -> int:
    # keeps held rows: page count of the full ledger list above
    statement = select(Transaction).where(Transaction.after_baseline())
    statement = apply_transaction_filters(
        session, statement, nature=nature, account_id=account_id,
        date_from=date_from, date_to=date_to, commitment_id=commitment_id,
        debt_id=debt_id, transaction_type=transaction_type, transaction_id=transaction_id,
        merchant_id=merchant_id, category_node=category_node, tag=tag, q=q,
    )
    return len(session.exec(statement).all())

def node_context(session: Session, transactions: list[Transaction]) -> tuple[dict, dict]:
    """(node_labels, row_flow) keyed by transaction id. Loads the whole (small)
    category tree once, so rows cost no queries. Unfiled rows get no label
    and a type-based flow."""
    nodes = {n.id: n for n in session.exec(select(CategoryNode)).all()}

    def label(node: CategoryNode) -> str:
        parts = [node.name]
        while node.parent_id:
            node = nodes[node.parent_id]
            parts.append(node.name)
        return " › ".join(reversed(parts))

    node_labels, row_flow = {}, {}
    for t in transactions:
        node = nodes.get(t.category_id) if t.category_id else None
        row_flow[t.id] = ("settled" if t.settled_by_id is not None
                          else "refund" if is_refund(t, node) else flow_of(t, node))
        if node is not None:
            node_labels[t.id] = label(node)
    return node_labels, row_flow

def attached_documents(session: Session, transactions: list[Transaction]) -> dict[int, list[tuple[int, str]]]:
    """{bank transaction id: [(document id, filename)]} of the bills and receipts settled by these rows."""
    ids = [t.id for t in transactions]
    if not ids:
        return {}
    out: dict[int, list[tuple[int, str]]] = {}
    for settled, document in session.exec(
            # keeps held rows: display lookup of the documents a bank row settled
            select(Transaction, Document).join(Document, Document.id == Transaction.document_id)
            .where(Transaction.settled_by_id.in_(ids))).all():
        out.setdefault(settled.settled_by_id, []).append((document.id, document.filename))
    return out


class UnknownCategory(ValueError):
    """A bulk edit named a category the tree does not have."""


def bulk_edit(session: Session, transaction_ids: list, category_slug: Optional[str], nature: Optional[str],
              account_id) -> None:
    """Files the ticked rows under one category, nature and/or account, then commits."""
    transactions = session.exec(
        # keeps held rows: bulk edit acts on the rows a person ticked, held or not
        select(Transaction).where(Transaction.id.in_(transaction_ids))
    ).all()
    target_node = None
    if category_slug:
        target_node = session.exec(select(CategoryNode).where(CategoryNode.slug == category_slug)).first()
        if target_node is None:
            raise UnknownCategory(category_slug)
    for t in transactions:
        if target_node is not None:
            file_transaction(session, t, target_node)
        if nature:
            t.nature = Nature(nature)
        if account_id:
            t.account_id = int(account_id)
        session.add(t)
    session.commit()


def commitment_from_merchant(session: Session, merchant: Merchant, cadence: Cadence, planned_amount: float,
                             year: Optional[int]) -> Commitment:
    """A new commitment named after the merchant; every row of that merchant is tied to it, and the merchant's
    recurring flag counts as reviewed."""
    commitment = Commitment(name=merchant.canonical_name, category_id=merchant.default_category_id,
                            cadence=cadence, planned_amount=planned_amount, year=year)
    session.add(commitment)
    session.commit()
    session.refresh(commitment)
    linked_transactions = session.exec(
        # keeps held rows: linking a merchant's rows to a new commitment (bookkeeping, not a total)
        select(Transaction).where(Transaction.merchant_id == merchant.id)
    ).all()
    for t in linked_transactions:
        t.commitment_id = commitment.id
        session.add(t)
    merchant.recurring_reviewed = True
    session.add(merchant)
    session.commit()
    return commitment
