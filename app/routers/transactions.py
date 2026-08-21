"""Routes for browsing and filtering transactions."""

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.models.account import Account
from app.models.transaction import Category, Nature, Transaction

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
