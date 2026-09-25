"""Human review of Wiki Lint findings. Lint never edits; the only edit here
is the explicit 'Add link' click (logged as WikiOperation.EDIT)."""

import json
from datetime import datetime

from anthropic import AsyncAnthropic
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, func, select

from app.config import settings
from app.db import get_session, get_session_factory
from app.models.wiki import WikiOperation, WikiPage
from app.models.wiki_lint import LintFinding, LintFindingKind, LintFindingStatus, LintRun
from app.services.wiki_lint.runner import LintAlreadyRunning, execute_run, start_run
from app.services.wiki_store import add_link, append_log
from app.templating import templates

router = APIRouter(prefix="/wiki/lint", tags=["wiki-lint"])

KIND_LABELS = {
    LintFindingKind.CONTRADICTION: "Facts that disagree",
    LintFindingKind.STALE_CLAIM: "Possibly out-of-date facts",
    LintFindingKind.GAP: "Missing information",
    LintFindingKind.STALE_LINK: "Links that may be out of date",
    LintFindingKind.STALE_SAVED_ANSWER: "Saved answers that may be out of date",
    LintFindingKind.UNSOURCED_CLAIM: "Facts without a current source",
    LintFindingKind.UNINGESTED_SOURCES: "Documents or records not yet read into the wiki",
    LintFindingKind.MISSING_LINK: "Missing cross-links",
    LintFindingKind.ORPHAN_PAGE: "Pages not connected to anything",
}


def get_lint_client() -> AsyncAnthropic:
    return AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


def open_finding_count(session: Session) -> int:
    return session.exec(select(func.count()).select_from(LintFinding)
                        .where(LintFinding.status == LintFindingStatus.OPEN)).one()


def _pages_for(session: Session, finding: LintFinding) -> list[WikiPage]:
    return [p for p in (session.get(WikiPage, i) for i in json.loads(finding.wiki_page_ids_json)) if p]


def _get_finding(session: Session, finding_id: int) -> LintFinding:
    finding = session.get(LintFinding, finding_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    return finding


def _close(session: Session, finding: LintFinding, status: LintFindingStatus, request: Request) -> HTMLResponse:
    finding.status, finding.resolved_at = status, datetime.utcnow()
    finding.resolved_by = getattr(request.state, "user_email", None) or "unknown"
    session.add(finding)
    session.commit()
    return HTMLResponse("")  # htmx removes the row


async def _execute_in_background(session_factory, run_id: int, client: AsyncAnthropic) -> None:
    with session_factory() as session:
        await execute_run(session, run_id, client)


@router.get("")
async def lint_page(request: Request, session: Session = Depends(get_session)):
    open_findings = session.exec(select(LintFinding).where(LintFinding.status == LintFindingStatus.OPEN)
                                 .order_by(LintFinding.created_at.desc())).all()
    groups = [(kind, label, [(f, _pages_for(session, f)) for f in open_findings if f.kind == kind])
              for kind, label in KIND_LABELS.items()]
    last_run = session.exec(select(LintRun).order_by(LintRun.started_at.desc())).first()
    return templates.TemplateResponse(request, "wiki/lint.html", {
        "groups": [g for g in groups if g[2]], "last_run": last_run, "open_count": len(open_findings),
        "message": request.query_params.get("message")})


@router.post("/run")
async def run_now(background_tasks: BackgroundTasks, session: Session = Depends(get_session),
                  session_factory=Depends(get_session_factory), client: AsyncAnthropic = Depends(get_lint_client)):
    try:
        run = start_run(session, "manual")
    except LintAlreadyRunning:
        return RedirectResponse("/wiki/lint?message=A+check+is+already+running", status_code=303)
    background_tasks.add_task(_execute_in_background, session_factory, run.id, client)
    return RedirectResponse("/wiki/lint?message=Check+started+-+refresh+in+a+minute", status_code=303)


@router.post("/findings/{finding_id}/dismiss")
async def dismiss(request: Request, finding_id: int, session: Session = Depends(get_session)):
    return _close(session, _get_finding(session, finding_id), LintFindingStatus.DISMISSED, request)


@router.post("/findings/{finding_id}/fixed")
async def mark_fixed(request: Request, finding_id: int, session: Session = Depends(get_session)):
    return _close(session, _get_finding(session, finding_id), LintFindingStatus.FIXED, request)


@router.post("/findings/{finding_id}/add-link")
async def add_missing_link(request: Request, finding_id: int, session: Session = Depends(get_session)):
    finding = _get_finding(session, finding_id)
    page_ids = json.loads(finding.wiki_page_ids_json)
    if finding.kind != LintFindingKind.MISSING_LINK or len(page_ids) != 2:
        raise HTTPException(status_code=400, detail="Only missing-link findings can add a link")
    if add_link(session, page_ids[0], page_ids[1]):
        append_log(session, WikiOperation.EDIT,
                   f"Added link wiki:{page_ids[0]} → wiki:{page_ids[1]} (wiki check finding {finding.id})",
                   page_ids=page_ids)
    return _close(session, finding, LintFindingStatus.FIXED, request)
