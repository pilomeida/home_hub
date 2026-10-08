"""Routes for browsing and filtering transactions."""

from typing import Optional

import json
from markupsafe import escape
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import NoResultFound
from sqlmodel import Session, select

from app.db import get_session
from app.models.account import Account
from app.models.category_node import CategoryNode
from app.models.commitment import Cadence, Commitment
from app.models.debt import Debt, DebtDirection
from app.models.person import Person
from app.models.position import DebtMatchRule
from app.services import debt_ledger as ledger
from app.models.merchant import Merchant
from app.models.transaction import Category, Nature, Transaction, TransactionType
from app.services.taxonomy_seed import LEGACY_TO_SLUG
from app.services.classification_engine import normalize_provider, get_needs_review_page, REVIEW_SECTIONS, unsorted_transactions_query
from app.services.taxonomy import (
    auto_fits, descendant_ids, direction_matches, file_transaction, flow_of, get_node, is_refund, legacy_category_for,
)
from app.templating import templates

router = APIRouter(prefix="/financials/transactions", tags=["transactions"])

_PAGE_SIZE = 100

def _apply_transaction_filters(
    session: Session,
    statement,
    category: Optional[str] = None,
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
):
    if tag:
        from app.services.tag_service import tag_node_ids
        node_ids = tag_node_ids(session, tag)
        statement = statement.where(Transaction.category_id.in_(node_ids if node_ids else [-1]))
    if category_node:
        node = session.exec(select(CategoryNode).where(CategoryNode.slug == category_node)).first()
        statement = statement.where(
            Transaction.category_id.in_(descendant_ids(session, node.id) if node else [])
        )
    if category:
        statement = statement.where(Transaction.category == Category(category))
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

def _filtered_transactions(
    session: Session,
    category: Optional[str] = None,
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
):
    statement = select(Transaction).order_by(Transaction.paid_date.desc(), Transaction.id.desc())
    statement = _apply_transaction_filters(
        session, statement, category=category, nature=nature, account_id=account_id,
        date_from=date_from, date_to=date_to, commitment_id=commitment_id,
        debt_id=debt_id, transaction_type=transaction_type, transaction_id=transaction_id,
        merchant_id=merchant_id, category_node=category_node, tag=tag,
    )
    statement = statement.limit(_PAGE_SIZE).offset((page - 1) * _PAGE_SIZE)
    return session.exec(statement).all()

def _lookup_dicts_for(session: Session, transactions: list[Transaction]) -> tuple[dict, dict, dict]:
    """Build {id: name}/{id: Transaction} lookups for the merchants,
    accounts, and linked (internal-transfer counterpart) transactions
    referenced by a batch of transactions, for row rendering. There is no
    SQLModel Relationship convention anywhere in this codebase's models, so
    rows are annotated via these dicts rather than introducing one here."""
    merchant_ids = {t.merchant_id for t in transactions if t.merchant_id is not None}
    linked_ids = {t.linked_transaction_id for t in transactions if t.linked_transaction_id is not None}
    linked_transactions = {
        lt.id: lt
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

def _count_filtered_transactions(
    session: Session,
    category: Optional[str] = None,
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
) -> int:
    statement = select(Transaction)
    statement = _apply_transaction_filters(
        session, statement, category=category, nature=nature, account_id=account_id,
        date_from=date_from, date_to=date_to, commitment_id=commitment_id,
        debt_id=debt_id, transaction_type=transaction_type, transaction_id=transaction_id,
        merchant_id=merchant_id, category_node=category_node, tag=tag,
    )
    return len(session.exec(statement).all())

def _node_context(session: Session, transactions: list[Transaction]) -> tuple[dict, dict]:
    """(node_labels, row_flow) keyed by transaction id. Loads the whole (small)
    category tree once, so rows cost no queries. Unfiled rows get no label
    (the template falls back to the legacy category) and a type-based flow."""
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
        row_flow[t.id] = "refund" if is_refund(t, node) else flow_of(t, node)
        if node is not None:
            node_labels[t.id] = label(node)
    return node_labels, row_flow

def _node_options(session: Session) -> list[dict]:
    nodes = session.exec(select(CategoryNode).order_by(CategoryNode.sort_order)).all()
    by_id = {n.id: n for n in nodes}
    options = []
    for n in nodes:
        parts, cur = [n.name], n
        while cur.parent_id:
            cur = by_id[cur.parent_id]
            parts.append(cur.name)
        options.append({"id": n.id, "slug": n.slug, "label": " › ".join(reversed(parts)), "name": n.name, "level": n.level})
    return options

@router.get("")
async def list_transactions(
    request: Request,
    category: Optional[str] = None,
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
    session: Session = Depends(get_session),
):
    transactions = _filtered_transactions(
        session, category, nature, account_id, date_from, date_to,
        commitment_id=commitment_id, debt_id=debt_id, transaction_type=transaction_type,
        page=page, transaction_id=transaction_id, merchant_id=merchant_id,
        category_node=category_node, tag=tag,
    )
    total_count = _count_filtered_transactions(
        session, category, nature, account_id, date_from, date_to,
        commitment_id=commitment_id, debt_id=debt_id, transaction_type=transaction_type,
        transaction_id=transaction_id, merchant_id=merchant_id,
        category_node=category_node, tag=tag,
    )
    total_pages = max(1, -(-total_count // _PAGE_SIZE))
    accounts = session.exec(select(Account)).all()
    merchant_names, account_names, linked_transactions = _lookup_dicts_for(session, transactions)
    node_labels, row_flow = _node_context(session, transactions)
    merchant_filter_name = merchant_names.get(merchant_id) if merchant_id else None
    if merchant_id and merchant_filter_name is None:
        merchant = session.get(Merchant, merchant_id)
        merchant_filter_name = merchant.canonical_name if merchant else None

    # Get all tags for the filter dropdown
    from app.services.tag_service import all_tags
    tags = all_tags(session)

    return templates.TemplateResponse(
        request,
        "transactions/list.html",
        {
            "transactions": transactions,
            "accounts": accounts,
            "categories": list(Category),
            "natures": list(Nature),
            "filters": {
                "category": category, "nature": nature, "account_id": account_id,
                "date_from": date_from, "date_to": date_to,
                "commitment_id": commitment_id, "debt_id": debt_id,
                "transaction_type": transaction_type, "merchant_id": merchant_id,
                "category_node": category_node, "tag": tag,
            },
            "merchant_filter_name": merchant_filter_name,
            "page": page,
            "total_pages": total_pages,
            "merchant_names": merchant_names,
            "account_names": account_names,
            "linked_transactions": linked_transactions,
            "node_options": _node_options(session),
            "node_labels": node_labels,
            "row_flow": row_flow,
            "tags": tags,
        },
    )

@router.post("/bulk-edit")
async def bulk_edit(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    transaction_ids = [int(v) for v in form.getlist("transaction_ids")]
    new_category = form.get("new_category") or None
    new_category_node = form.get("new_category_node") or None
    new_nature = form.get("new_nature") or None
    new_account_id = form.get("new_account_id") or None

    filter_category = form.get("category") or None
    filter_category_node = form.get("category_node") or None
    filter_tag = form.get("tag") or None
    filter_nature = form.get("nature") or None
    filter_account_id = form.get("account_id") or None
    filter_date_from = form.get("date_from") or None
    filter_date_to = form.get("date_to") or None
    filter_page = form.get("page") or None

    if transaction_ids:
        transactions = session.exec(
            select(Transaction).where(Transaction.id.in_(transaction_ids))
        ).all()
        target_node = None
        if new_category_node:
            target_node = session.exec(select(CategoryNode).where(CategoryNode.slug == new_category_node)).first()
            if target_node is None:
                raise HTTPException(status_code=400, detail="Unknown category")
        elif new_category:
            # Legacy parameter: only honoured when it maps to one tree leaf, so
            # category and category_id can never disagree.
            try:
                legacy_slug = LEGACY_TO_SLUG.get(Category(new_category))
            except ValueError:
                legacy_slug = None
            if not legacy_slug:
                raise HTTPException(status_code=400, detail="Ambiguous or unknown category; pick a tree category")
            target_node = get_node(session, legacy_slug)
        for t in transactions:
            if target_node is not None:
                file_transaction(session, t, target_node)
            if new_nature:
                t.nature = Nature(new_nature)
            if new_account_id:
                t.account_id = int(new_account_id)
            session.add(t)
        session.commit()

    transactions = _filtered_transactions(
        session,
        category=filter_category,
        category_node=filter_category_node,
        tag=filter_tag,
        nature=filter_nature,
        account_id=int(filter_account_id) if filter_account_id else None,
        date_from=filter_date_from,
        date_to=filter_date_to,
        page=int(filter_page) if filter_page else 1,
    )
    merchant_names, account_names, linked_transactions = _lookup_dicts_for(session, transactions)
    node_labels, row_flow = _node_context(session, transactions)
    return templates.TemplateResponse(
        request,
        "transactions/_rows.html",
        {
            "transactions": transactions, "merchant_names": merchant_names, "account_names": account_names,
            "linked_transactions": linked_transactions, "node_labels": node_labels, "row_flow": row_flow,
        },
    )

def _debt_candidate_options(session: Session, candidates: list) -> dict:
    """Per debt candidate: the informal debts it can be added to (person, direction,
    balance and the role it would get) and the direction a new debt would default to."""
    people = {p.id: p.name for p in session.exec(select(Person)).all()}
    debts = session.exec(
        select(Debt).where(Debt.external_number.is_(None), Debt.status == "active").order_by(Debt.id)
    ).all()
    out = {}
    for t in candidates:
        direction = ledger.derived_direction(t.transaction_type)
        out[t.id] = {
            "direction": direction.value if direction else None,
            "debts": [
                {"id": d.id, "person": people.get(d.person_id) or "Unnamed",
                 "lent": d.direction == DebtDirection.OWED_TO_US, "balance": d.current_balance,
                 "role": ledger.default_kind(d.direction, t.transaction_type),
                 "suggested": ledger.transfer_default_kind(d.direction)}
                for d in debts
            ],
        }
    return out


def _review_offsets(source) -> dict[str, int]:
    """The page the user is on in each Needs Review list (m_off, r_off, ...), taken
    from the query string or the form so an action re-renders the same page."""
    out = {}
    for key in REVIEW_SECTIONS:
        raw = str(source.get(f"{key}_off") or "0")
        out[key] = int(raw) if raw.isdigit() else 0
    return out


def _needs_review_context(session: Session, offsets: Optional[dict[str, int]] = None) -> dict:
    page = get_needs_review_page(session, offsets)
    queue = page.queue
    nodes = _node_options(session)
    # Rows carry only the suggested node; the full list loads on first focus.
    return {
        "queue": queue,
        "sections": page.sections,
        "offsets_json": json.dumps({f"{k}_off": v.offset for k, v in page.sections.items()}),
        "debt_options": _debt_candidate_options(session, queue.debt_candidates),
        "node_by_id": {o["id"]: o for o in nodes},
        "categories": list(Category),
        "natures": list(Nature),
    }


def _review_rows(request: Request, session: Session, source=None):
    offsets = _review_offsets(source) if source is not None else None
    return templates.TemplateResponse(
        request, "transactions/_needs_review_rows.html", _needs_review_context(session, offsets)
    )


@router.get("/needs-review")
async def needs_review(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request, "transactions/needs_review.html",
        _needs_review_context(session, _review_offsets(request.query_params)),
    )


@router.get("/needs-review/rows")
async def needs_review_rows(request: Request, session: Session = Depends(get_session)):
    return _review_rows(request, session, request.query_params)


@router.get("/node-options")
async def node_options(session: Session = Depends(get_session)):
    """<option> elements for every level-3 node, grouped per level-1 group, loaded
    by a Needs Review select the first time it is focused."""
    groups: dict[str, list[str]] = {}
    for o in _node_options(session):
        if o["level"] != 3 or o["slug"].startswith("unsorted"):
            continue
        group = o["label"].split(" \u203a ")[0]
        groups.setdefault(group, []).append(
            f'<option value="{escape(o["slug"])}">{escape(o["label"])}</option>'
        )
    body = [f'<option value="">(choose)</option>']
    for group, rows in groups.items():
        body.append(f'<optgroup label="{escape(group)}">{"".join(rows)}</optgroup>')
    return HTMLResponse("".join(body), headers={"Cache-Control": "private, max-age=600"})

@router.post("/merchants/{merchant_id}/confirm")
async def confirm_merchant(request: Request, merchant_id: int, session: Session = Depends(get_session)):
    merchant = session.get(Merchant, merchant_id)
    if merchant is None:
        raise HTTPException(status_code=404, detail="Merchant not found")

    form = await request.form()
    node_slug = form.get("category_node") or None
    new_nature = form.get("nature") or None
    if node_slug:
        try:
            node = get_node(session, node_slug)
        except NoResultFound:
            raise HTTPException(status_code=400, detail="Unknown category")
        merchant.default_category_id = node.id
        merchant.default_category = legacy_category_for(session, node)
        # One click fixes the merchant's history: file everything not yet filed
        # (or parked in Unsorted) whose direction fits the node. Deliberate
        # filings and mismatched directions are left alone.
        for t in session.exec(unsorted_transactions_query().where(Transaction.merchant_id == merchant.id)).all():
            if auto_fits(session, t, node):
                file_transaction(session, t, node)
    if new_nature:
        merchant.default_nature = Nature(new_nature)

    # No category chosen and none on record: the merchant still needs one, so
    # it must not drop out of the review queue.
    if node_slug or merchant.default_category_id is not None:
        merchant.confirmed = True
    session.add(merchant)
    session.commit()
    return _review_rows(request, session, form)

@router.post("/merchants/{merchant_id}/dismiss-recurring")
async def dismiss_recurring(request: Request, merchant_id: int, session: Session = Depends(get_session)):
    form = await request.form()
    merchant = session.get(Merchant, merchant_id)
    if merchant is None:
        raise HTTPException(status_code=404, detail="Merchant not found")
    merchant.recurring_reviewed = True
    session.add(merchant)
    session.commit()
    return _review_rows(request, session, form)

@router.post("/{transaction_id}/dismiss-debt-candidate")
async def dismiss_debt_candidate(request: Request, transaction_id: int, session: Session = Depends(get_session)):
    form = await request.form()
    transaction = session.get(Transaction, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    transaction.debt_candidate_reviewed = True
    session.add(transaction)
    session.commit()
    return _review_rows(request, session, form)

@router.post("/merchants/{merchant_id}/create-commitment")
async def create_commitment_from_merchant(
    request: Request, merchant_id: int, session: Session = Depends(get_session)
):
    form = await request.form()
    merchant = session.get(Merchant, merchant_id)
    if merchant is None:
        raise HTTPException(status_code=404, detail="Merchant not found")

    commitment = Commitment(
        name=merchant.canonical_name,
        category=merchant.default_category,
        cadence=Cadence(form["cadence"]),
        planned_amount=float(form["planned_amount"]),
        year=int(form["year"]) if form.get("year") else None,
    )
    session.add(commitment)
    session.commit()
    session.refresh(commitment)

    linked_transactions = session.exec(
        select(Transaction).where(Transaction.merchant_id == merchant_id)
    ).all()
    for t in linked_transactions:
        t.commitment_id = commitment.id
        session.add(t)
    merchant.recurring_reviewed = True
    session.add(merchant)
    session.commit()

    return _review_rows(request, session, form)

def _plain(status: int, message: str) -> PlainTextResponse:
    """Plain-text error body: the Needs Review page shows it as is."""
    return PlainTextResponse(message, status_code=status)


@router.post("/{transaction_id}/link-debt")
async def link_debt(request: Request, transaction_id: int, session: Session = Depends(get_session)):
    form = await request.form()
    transaction = session.get(Transaction, transaction_id)
    if transaction is None:
        return _plain(404, "Transaction not found")
    role = (form.get("role") or "").strip() or None
    if role is not None and role not in ledger.ROLES:
        return _plain(400, "The role must be advance or repayment")
    if transaction.debt_id is not None:
        return _plain(400, "This transaction is already linked to a debt")

    existing_debt_id = (form.get("existing_debt_id") or "").strip()
    if existing_debt_id:
        debt = session.get(Debt, int(existing_debt_id)) if existing_debt_id.isdigit() else None
        if debt is None:
            return _plain(404, "Debt not found")
        if debt.external_number is not None:
            # A statement loan: a plain link, no ledger and no counterparty rule.
            transaction.debt_id = debt.id
            transaction.debt_candidate_reviewed = True
            session.add(transaction)
            session.commit()
            return _review_rows(request, session, form)
        entry_kind = role
    else:
        direction = form.get("direction") or None
        try:
            direction = DebtDirection(direction) if direction else ledger.derived_direction(transaction.transaction_type)
        except ValueError:
            return _plain(400, "Unknown direction")
        if direction is None:
            return _plain(400, "Choose the direction: a transfer does not say whether you lent or borrowed")
        person_name = (form.get("person_name") or "").strip()
        if len(person_name) > 80:
            return _plain(400, "The name is at most 80 characters")
        entry_kind = role or "advance"  # the creating transaction is the loan's first advance
        if transaction.transaction_type != TransactionType.TRANSFER \
                and ledger.default_kind(direction, transaction.transaction_type) != entry_kind:
            return _plain(400, "The role does not fit this transaction and direction: money going out "
                               "starts a loan we gave, money coming in starts a loan we received")
        try:
            debt = ledger.create_informal_debt(session, person_name, direction)
        except ValueError as exc:
            session.rollback()
            return _plain(400, str(exc))

    try:
        ledger.link_transaction_as_entry(session, debt, transaction, entry_kind)
    except ValueError as exc:
        session.rollback()
        return _plain(400, str(exc))

    # Informal debts learn the counterparty, so later transactions from it
    # link automatically; statement loans never get rules.
    key = normalize_provider(transaction.provider)
    if key and session.exec(select(DebtMatchRule).where(DebtMatchRule.normalized_key == key)).first() is None:
        session.add(DebtMatchRule(debt_id=debt.id, normalized_key=key))
    session.commit()

    return _review_rows(request, session, form)
