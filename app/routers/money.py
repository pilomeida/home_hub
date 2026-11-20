"""Money (PLAN step 10d): /money (headline row, spending breakdown, its trend) and /money/history (one category over
time). The other Money tabs are the pages that already exist (Bills & due, Loans, Utilities, All lines)."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session

from app.db import get_session
from app.services import money_page as mp
from app.templating import templates

router = APIRouter(prefix="/money", tags=["money"])


@router.get("")
async def money(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "money/page.html", {"page": mp.money_page(session, date.today())})


_NO_CATEGORY = {"what": "Not shown.", "why": "There is no spending category with that name.",
                "next_step": "Pick a category from the list on the Category history page."}


@router.get("/history")
async def history(request: Request, category: str = "", months: int = 12, view: str = "columns",
                  session: Session = Depends(get_session)):
    choices = mp.category_choices(session)
    category = category or next((s for s, _ in choices if s != mp.UNSORTED), mp.UNSORTED)
    months = months if months in mp.MONTH_CHOICES else 12
    view = view if view in mp.VIEWS else "columns"
    found = mp.category_history(session, category, months, date.today())
    if found is None:
        raise HTTPException(status_code=404, detail=_NO_CATEGORY)
    return templates.TemplateResponse(request, "money/history.html", {
        "h": found, "choices": choices, "months": months, "view": view, "views": mp.VIEWS,
        "month_choices": mp.MONTH_CHOICES})
