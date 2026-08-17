"""Dashboard home route and health check."""

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db import get_session
from app.services.dashboard_service import get_dashboard_data

router = APIRouter(tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/")
async def dashboard(request: Request, session: Session = Depends(get_session)):
    data = get_dashboard_data(session)
    return templates.TemplateResponse(request, "dashboard.html", {"data": data})
