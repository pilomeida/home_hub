"""Routes for the Utilities domain (Electricity / Water / Telecom)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.db import get_session
from app.models.utility_reading import UtilityReading, UtilityType

router = APIRouter(prefix="/utilities", tags=["utilities"])
templates = Jinja2Templates(directory="app/templates")

_VALID_TABS = {
    "electricity": UtilityType.ELECTRICITY,
    "water": UtilityType.WATER,
    "telecom": UtilityType.TELECOM,
}


def _build_charts(readings: list[UtilityReading]) -> dict:
    def series(field: str) -> dict:
        rows = [(r.period_label, getattr(r, field)) for r in readings]
        values = [v for _, v in rows if v is not None]
        return {"rows": rows, "max": max(values) if values else 0}

    return {
        "consumption": series("consumption_value"),
        "cost_total": series("cost_total"),
        "cost_per_unit": series("cost_per_unit"),
        "energy_cost": series("energy_cost"),
        "power_cost": series("power_cost"),
        "fees_taxes_cost": series("fees_taxes_cost"),
        "vat_cost": series("vat_cost"),
    }


@router.get("")
async def utilities_root():
    return RedirectResponse("/utilities/electricity")


@router.get("/{tab}")
async def utility_tab(request: Request, tab: str, session: Session = Depends(get_session)):
    if tab not in _VALID_TABS:
        raise HTTPException(status_code=404, detail="Unknown utility tab")

    readings = session.exec(
        select(UtilityReading)
        .where(UtilityReading.utility_type == _VALID_TABS[tab])
        .order_by(UtilityReading.period_label)
    ).all()

    return templates.TemplateResponse(
        request,
        "utilities/tab.html",
        {"active_tab": tab, "readings": readings, "charts": _build_charts(readings)},
    )
