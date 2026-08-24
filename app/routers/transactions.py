"""Routes for browsing and filtering transactions."""

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.models.account import Account
from app.models.commitment import Cadence, Commitment
from app.models.debt import Debt, DebtDirection, DebtKind
from app.models.merchant import Merchant
from app.models.person import Person
from app.models.transaction import Category, Nature, Transaction
from app.services.classification_engine import get_needs_review_queue

router = APIRouter(prefix="/transactions", tags=["transactions"])
templates = Jinja2Templates(directory="app/templates")


_PAGE_SIZE = 100


def _apply_transaction_filters(
    statement,
    category: Optional[str] = None,
    nature: Optional[str] = None,
    account_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    transaction_id: Optional[int] = None,
):
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
    if transaction_id:
        statement = statement.where(Transaction.id == transaction_id)
    return statement


def _filtered_transactions(
    session: Session,
    category: Optional[str] = None,
    nature: Optional[str] = None,
    account_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    transaction_id: Optional[int] = None,
):
    statement = select(Transaction).order_by(Transaction.paid_date.desc(), Transaction.id.desc())
    statement = _apply_transaction_filters(
        statement, category=category, nature=nature, account_id=account_id,
        date_from=date_from, date_to=date_to, transaction_id=transaction_id,
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
    transaction_id: Optional[int] = None,
) -> int:
    statement = select(Transaction)
    statement = _apply_transaction_filters(
        statement, category=category, nature=nature, account_id=account_id,
        date_from=date_from, date_to=date_to, transaction_id=transaction_id,
    )
    return len(session.exec(statement).all())


@router.get("")
async def list_transactions(
    request: Request,
    category: Optional[str] = None,
    nature: Optional[str] = None,
    account_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    transaction_id: Optional[int] = None,
    session: Session = Depends(get_session),
):
    transactions = _filtered_transactions(
        session, category, nature, account_id, date_from, date_to, page=page,
        transaction_id=transaction_id,
    )
    total_count = _count_filtered_transactions(
        session, category, nature, account_id, date_from, date_to, transaction_id=transaction_id,
    )
    total_pages = max(1, -(-total_count // _PAGE_SIZE))
    accounts = session.exec(select(Account)).all()
    merchant_names, account_names, linked_transactions = _lookup_dicts_for(session, transactions)
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
            },
            "page": page,
            "total_pages": total_pages,
            "merchant_names": merchant_names,
            "account_names": account_names,
            "linked_transactions": linked_transactions,
        },
    )


@router.post("/bulk-edit")
async def bulk_edit(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    transaction_ids = [int(v) for v in form.getlist("transaction_ids")]
    new_category = form.get("new_category") or None
    new_nature = form.get("new_nature") or None
    new_account_id = form.get("new_account_id") or None

    filter_category = form.get("category") or None
    filter_nature = form.get("nature") or None
    filter_account_id = form.get("account_id") or None
    filter_date_from = form.get("date_from") or None
    filter_date_to = form.get("date_to") or None
    filter_page = form.get("page") or None

    if transaction_ids:
        transactions = session.exec(
            select(Transaction).where(Transaction.id.in_(transaction_ids))
        ).all()
        for t in transactions:
            if new_category:
                t.category = Category(new_category)
            if new_nature:
                t.nature = Nature(new_nature)
            if new_account_id:
                t.account_id = int(new_account_id)
            session.add(t)
        session.commit()

    transactions = _filtered_transactions(
        session,
        category=filter_category,
        nature=filter_nature,
        account_id=int(filter_account_id) if filter_account_id else None,
        date_from=filter_date_from,
        date_to=filter_date_to,
        page=int(filter_page) if filter_page else 1,
    )
    merchant_names, account_names, linked_transactions = _lookup_dicts_for(session, transactions)
    return templates.TemplateResponse(
        request,
        "transactions/_rows.html",
        {
            "transactions": transactions, "merchant_names": merchant_names, "account_names": account_names,
            "linked_transactions": linked_transactions,
        },
    )


@router.get("/needs-review")
async def needs_review(request: Request, session: Session = Depends(get_session)):
    queue = get_needs_review_queue(session)
    return templates.TemplateResponse(request, "transactions/needs_review.html", {"queue": queue})


@router.post("/merchants/{merchant_id}/confirm")
async def confirm_merchant(request: Request, merchant_id: int, session: Session = Depends(get_session)):
    merchant = session.get(Merchant, merchant_id)
    if merchant is None:
        raise HTTPException(status_code=404, detail="Merchant not found")
    merchant.confirmed = True
    session.add(merchant)
    session.commit()
    queue = get_needs_review_queue(session)
    return templates.TemplateResponse(request, "transactions/_needs_review_rows.html", {"queue": queue})


@router.post("/merchants/{merchant_id}/dismiss-recurring")
async def dismiss_recurring(request: Request, merchant_id: int, session: Session = Depends(get_session)):
    merchant = session.get(Merchant, merchant_id)
    if merchant is None:
        raise HTTPException(status_code=404, detail="Merchant not found")
    merchant.recurring_reviewed = True
    session.add(merchant)
    session.commit()
    queue = get_needs_review_queue(session)
    return templates.TemplateResponse(request, "transactions/_needs_review_rows.html", {"queue": queue})


@router.post("/{transaction_id}/dismiss-debt-candidate")
async def dismiss_debt_candidate(request: Request, transaction_id: int, session: Session = Depends(get_session)):
    transaction = session.get(Transaction, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    transaction.debt_candidate_reviewed = True
    session.add(transaction)
    session.commit()
    queue = get_needs_review_queue(session)
    return templates.TemplateResponse(request, "transactions/_needs_review_rows.html", {"queue": queue})


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

    queue = get_needs_review_queue(session)
    return templates.TemplateResponse(request, "transactions/_needs_review_rows.html", {"queue": queue})


@router.post("/{transaction_id}/link-debt")
async def link_debt(request: Request, transaction_id: int, session: Session = Depends(get_session)):
    form = await request.form()
    transaction = session.get(Transaction, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    existing_debt_id = form.get("existing_debt_id")
    if existing_debt_id:
        debt = session.get(Debt, int(existing_debt_id))
        if debt is None:
            raise HTTPException(status_code=404, detail="Debt not found")
    else:
        person_id = None
        person_name = form.get("person_name")
        if person_name:
            person = session.exec(select(Person).where(Person.name == person_name)).first()
            if person is None:
                person = Person(name=person_name)
                session.add(person)
                session.commit()
                session.refresh(person)
            person_id = person.id
        debt = Debt(
            kind=DebtKind.INFORMAL,
            person_id=person_id,
            direction=DebtDirection(form["direction"]) if form.get("direction") else None,
            original_amount=transaction.amount,
            current_balance=Decimal(str(transaction.amount)),
        )
        session.add(debt)
        session.commit()
        session.refresh(debt)

    transaction.debt_id = debt.id
    transaction.debt_candidate_reviewed = True
    session.add(transaction)
    session.commit()

    queue = get_needs_review_queue(session)
    return templates.TemplateResponse(request, "transactions/_needs_review_rows.html", {"queue": queue})
