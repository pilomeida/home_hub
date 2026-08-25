"""Dashboard home route and health check."""

from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db import get_session
from app.services.household_service import get_household_data
from app.services.overview_service import get_overview_data, get_period_dependent_data

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/")
async def dashboard(request: Request, session: Session = Depends(get_session)):
    overview = get_overview_data(session)
    household = get_household_data(session)
    return templates.TemplateResponse(
        request, "dashboard.html",
        {"overview": overview, "household": household, "today": date.today()},
    )


@router.get("/overview/period")
async def overview_period(request: Request, range: str = "12m", session: Session = Depends(get_session)):
    # Every range-pill click hits this route -- compute only the cash-flow
    # chart and the category comparison table (not the full Overview: KPIs,
    # narrative, needs attention) so re-rendering these two period-linked
    # sections doesn't pay for 5 KPI cards' worth of unrelated aggregation
    # on every click. Both sections share one range so "Where it went"
    # always reflects the same period as the chart above it.
    today = date.today()
    chart, rows = get_period_dependent_data(session, today, range)
    return templates.TemplateResponse(
        request, "dashboard/_period_panel.html", {"chart": chart, "rows": rows},
    )
