"""Routes for browsing and filtering transactions."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.models.account import Account
from app.models.merchant import Merchant
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
