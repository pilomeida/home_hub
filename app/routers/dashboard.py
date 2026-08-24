"""Dashboard home route and health check."""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db import get_session
from app.services.household_service import get_household_data
from app.services.overview_service import get_overview_data

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
        request, "dashboard.html", {"overview": overview, "household": household},
    )


@router.get("/cash-flow-chart")
async def cash_flow_chart(request: Request, range: str = "12m", session: Session = Depends(get_session)):
    overview = get_overview_data(session, cash_flow_range=range)
    return templates.TemplateResponse(
        request, "dashboard/_cash_flow_chart.html", {"chart": overview.cash_flow_chart},
    )
