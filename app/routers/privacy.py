"""Privacy settings screen: which tier each kind of document has, and the
switch that makes tiers binding (decision D12). Everything shown comes from
the registry and the database; no document type is named here."""

from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.db import get_session
from app.llm_gateway import GatewayClient, GatewayError, get_gateway
from app.privacy.jobs import PRIVACY_LABELS, QUALITY_LABELS, job_label
from app.privacy import settings as ps
from app.privacy.tiers import Tier, TierBlocked
from app.templating import templates

router = APIRouter(prefix="/privacy", tags=["privacy"])

TIER_LABELS = {
    Tier.LOW.value: "Low — any AI service",
    Tier.MID.value: "Mid — zero-retention, US/EU providers only",
    Tier.HIGH.value: "High — highly sensitive (no route yet: nothing is read)",
}


def get_privacy_gateway() -> GatewayClient:
    """The gateway client for the screen's AI-jobs section. Tests override this dependency."""
    return get_gateway()


def _by(request: Request) -> Optional[str]:
    return getattr(request.state, "user_email", None)


def _back() -> RedirectResponse:
    return RedirectResponse("/privacy", status_code=303)


@router.get("")
async def settings_page(request: Request, session: Session = Depends(get_session),
                        gateway: GatewayClient = Depends(get_privacy_gateway), saved: str = "", problem: str = ""):
    rows = ps.type_rows(session)
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r.info.domain_label, []).append(r)
    jobs, jobs_error = [], ""
    try:
        for p in await gateway.pairings():
            label, blurb = job_label(p["workload_type"])
            jobs.append({**p, "label": label, "blurb": blurb})
    except GatewayError:
        jobs_error = "Can't reach the AI gateway right now, so the AI jobs can't be shown. Try again in a minute."
    return templates.TemplateResponse(request, "privacy/settings.html", {
        "groups": groups,
        "tier_labels": TIER_LABELS,
        "enforcing": ps.enforcing(session),
        "unset_count": sum(1 for r in rows if r.tier is None),
        "changes": ps.recent_changes(session),
        "jobs": jobs, "jobs_error": jobs_error, "saved_job": saved, "job_problem": problem,
        "privacy_labels": PRIVACY_LABELS, "quality_labels": QUALITY_LABELS,
    })


@router.post("/types")
def save_type(request: Request, domain: str = Form(...), category: str = Form(...),
              tier: str = Form(""), redact: Optional[str] = Form(None),
              session: Session = Depends(get_session)):
    try:
        ps.save_type(session, domain, category, tier, redact is not None, _by(request))
    except TierBlocked:
        pass   # an unknown type or tier from a stale form: nothing saved
    return _back()


@router.post("/confirm-suggestions")
def confirm_suggestions(request: Request, session: Session = Depends(get_session)):
    ps.confirm_suggestions(session, _by(request))
    return _back()


@router.post("/enforce")
def enforce(request: Request, on: str = Form(...), session: Session = Depends(get_session)):
    ps.set_enforcing(session, on == "1", _by(request))
    return _back()


@router.post("/jobs")
async def save_job(workload_type: str = Form(...), privacy_tier: str = Form(""), min_quality_tier: str = Form(""),
                   gateway: GatewayClient = Depends(get_privacy_gateway)):
    """Both values are always sent: the form shows the current setting, "standard" means inherit."""
    try:
        await gateway.set_pairing_tiers(workload_type, privacy_tier=privacy_tier or None,
                                        min_quality_tier=min_quality_tier or None)
    except GatewayError as exc:
        return RedirectResponse("/privacy?" + urlencode({"problem": _friendly(exc), "saved": workload_type}), status_code=303)
    return RedirectResponse("/privacy?" + urlencode({"saved": workload_type}), status_code=303)


def _friendly(exc: GatewayError) -> str:
    if exc.status == 400:
        return "That setting would leave this job with no AI model allowed to do it, so nothing was changed."
    if exc.status == 0:
        return "Can't reach the AI gateway right now. Nothing was changed."
    return "The AI gateway refused that change. Nothing was changed."
