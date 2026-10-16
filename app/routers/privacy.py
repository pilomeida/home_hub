"""Privacy settings screen: which tier each kind of document has, and the
switch that makes tiers binding (decision D12). Everything shown comes from
the registry and the database; no document type is named here."""

from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.db import get_session
from app.llm_gateway import GatewayClient, GatewayError, get_gateway
from app.privacy.jobs import PRIVACY_LABELS, QUALITY_LABELS, job_label
from app.models.privacy import DocumentFingerprint
from app.privacy import settings as ps
from app.problems import Problem
from app.privacy.recognise import KINDS
from app.privacy.tiers import Tier, TierBlocked
from app.templating import templates

router = APIRouter(prefix="/privacy", tags=["privacy"])

TIER_LABELS = {
    Tier.LOW.value: "Low — any AI service",
    Tier.MID.value: "Mid — zero-retention, US/EU providers only",
    Tier.HIGH.value: "High — highly sensitive: confidential-computing service only (Ask and the wiki check do not read these)",
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
                        gateway: GatewayClient = Depends(get_privacy_gateway), saved: str = "", problem: str = "", refused: str = ""):
    rows = ps.type_rows(session)
    type_names = {(r.info.domain, r.info.category): f"{r.info.domain_label} › {r.info.label}" for r in rows}
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
        "groups": groups, "refused": _refusal(refused),
        "tier_labels": TIER_LABELS,
        "enforcing": ps.enforcing(session),
        "unset_count": sum(1 for r in rows if r.tier is None),
        "changes": ps.recent_changes(session),
        "jobs": jobs, "jobs_error": jobs_error, "saved_job": saved, "job_problem": problem,
        "privacy_labels": PRIVACY_LABELS, "quality_labels": QUALITY_LABELS,
        "rules": [(r, type_names.get((r.domain, r.category), f"{r.domain}/{r.category}"))
                  for r in session.exec(select(DocumentFingerprint).order_by(DocumentFingerprint.id))],
        "kinds": KINDS, "type_choices": [(f"{i.domain}/{i.category}", f"{i.domain_label} › {i.label}") for i in ps.known_types()],
    })


def _refusal(code: str) -> Optional[Problem]:
    """The three-part message for a level change the Hub refused (the code comes from our own redirect)."""
    if code == "forced":
        return Problem("Not saved.", "Health papers are always High, so their level cannot be changed.",
                       "Nothing to do: they are already as private as the Hub can make them.")
    if code == "stale":
        return Problem("Not saved.", "This page was out of date: that kind of document or level is not known any more.",
                       "Reload the Privacy page and try the change again.")
    return None


@router.post("/types")
def save_type(request: Request, domain: str = Form(...), category: str = Form(...),
              tier: str = Form(""), redact: Optional[str] = Form(None),
              session: Session = Depends(get_session)):
    try:
        ps.save_type(session, domain, category, tier, redact is not None, _by(request))
    except TierBlocked:
        # nothing was saved: either Health (forced High) or an unknown type or tier from a stale form
        return RedirectResponse("/privacy?" + urlencode({"refused": "forced" if ps.forced_tier(domain) else "stale"}),
                                status_code=303)
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


@router.post("/rules")
def add_rule(request: Request, kind: str = Form(...), pattern: str = Form(...), doc_type: str = Form(...),
             session: Session = Depends(get_session)):
    domain, _, category = doc_type.partition("/")
    valid = {(i.domain, i.category) for i in ps.known_types()}
    if kind in KINDS and pattern.strip() and (domain, category) in valid:
        session.add(DocumentFingerprint(kind=kind, pattern=pattern.strip().lower(), domain=domain, category=category,
                                        note="Added on the Privacy page", created_by=_by(request)))
        session.commit()
    return _back()


@router.post("/rules/{rule_id}/delete")
def delete_rule(rule_id: int, session: Session = Depends(get_session)):
    rule = session.get(DocumentFingerprint, rule_id)
    if rule is not None:
        session.delete(rule)
        session.commit()
    return _back()
