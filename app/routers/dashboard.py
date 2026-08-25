"""Dashboard home route and health check."""

from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db import get_session
from app.services.household_service import get_household_data
from app.services.overview_charts import build_cash_flow_chart
from app.services.overview_service import _monthly_flow_totals, get_overview_data

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


@router.get("/cash-flow-chart")
async def cash_flow_chart(request: Request, range: str = "12m", session: Session = Depends(get_session)):
    # Every range-pill click hits this route -- compute only the cash-flow
    # chart's own inputs (not the full Overview: KPIs, category comparison,
    # needs attention, narrative) so re-rendering a 6-12 bar chart doesn't
    # pay for 5 KPI cards' worth of unrelated aggregation on every click.
    today = date.today()
    income, expense = _monthly_flow_totals(session, today)
    chart = build_cash_flow_chart(income, expense, range, today)
    return templates.TemplateResponse(
        request, "dashboard/_cash_flow_chart.html", {"chart": chart},
    )
