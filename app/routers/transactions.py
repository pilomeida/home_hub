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


def _filtered_transactions(
    session: Session,
    category: Optional[str] = None,
    nature: Optional[str] = None,
    account_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    statement = select(Transaction).order_by(Transaction.paid_date.desc(), Transaction.id.desc())
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
    return session.exec(statement).all()


@router.get("")
async def list_transactions(
    request: Request,
    category: Optional[str] = None,
    nature: Optional[str] = None,
    account_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    session: Session = Depends(get_session),
):
    transactions = _filtered_transactions(session, category, nature, account_id, date_from, date_to)
    accounts = session.exec(select(Account)).all()
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
        },
    )


@router.post("/bulk-edit")
async def bulk_edit(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    transaction_ids = [int(v) for v in form.getlist("transaction_ids")]
    new_category = form.get("new_category") or None
    new_nature = form.get("new_nature") or None
    new_account_id = form.get("new_account_id") or None

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

    transactions = _filtered_transactions(session)
    return templates.TemplateResponse(request, "transactions/_rows.html", {"transactions": transactions})


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
