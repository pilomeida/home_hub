"""Dashboard home route and health check."""

from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlmodel import Session

from app.db import get_session
from app.services.budget_service import get_budget_overview, set_budget
from app.services.domain_overview import build_domain_cards
from app.services.household_service import get_household_data
from app.services.overview_service import get_overview_data, get_period_dependent_data
from app.templating import templates

router = APIRouter(tags=["dashboard"])

@router.get("/health")
async def health():
    return {"status": "ok"}

@router.get("/")
async def dashboard(request: Request, session: Session = Depends(get_session)):
    overview = get_overview_data(session)
    household = get_household_data(session)
    return templates.TemplateResponse(
        request, "dashboard.html",
        {"overview": overview, "household": household, "domain_cards": build_domain_cards(session, date.today()),
         "today": date.today(), **_budget_context(session, "month")},
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


def _budget_context(session: Session, view: str) -> dict:
    today = date.today()
    ov = get_budget_overview(session, today, view)
    lines = [l for g in ov.groups for c in g.categories for l in c.lines]
    return {"budget": ov, "budget_view": view, "budget_has_lines": bool(lines),
            "budget_has_budgets": ov.has_budgets}


def _budget_panel(request: Request, session: Session, view: str):
    return templates.TemplateResponse(
        request, "dashboard/_budget_panel.html", _budget_context(session, view),
    )


def _bad_request(message: str) -> PlainTextResponse:
    return PlainTextResponse(message, status_code=400)


@router.get("/overview/budget")
async def overview_budget(request: Request, view: str = "month", session: Session = Depends(get_session)):
    if view not in ("month", "year"):
        return _bad_request("view must be 'month' or 'year'")
    return _budget_panel(request, session, view)


@router.post("/overview/budget/{node_id}")
async def overview_budget_save(node_id: int, request: Request, session: Session = Depends(get_session)):
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    view = form.get("view") or "month"
    if view not in ("month", "year"):
        return _bad_request("view must be 'month' or 'year'")
    try:
        amount = float((form.get("amount") or "").replace(",", "."))
        if amount != amount or amount in (float("inf"), float("-inf")):
            raise ValueError
    except ValueError:
        return _bad_request("amount must be a number")
    raw_month = (form.get("expected_month") or "").strip()
    try:
        expected_month = int(raw_month) if raw_month else None
    except ValueError:
        return _bad_request("expected_month must be a whole number from 1 to 12")
    try:
        set_budget(session, node_id, date.today().year, amount, expected_month)
    except ValueError as exc:
        return _bad_request(str(exc))
    return _budget_panel(request, session, view)
