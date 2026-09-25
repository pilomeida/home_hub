"""Runs a lint pass and reconciles with earlier runs:
- same fingerprint OPEN      -> stays open (last_seen and wording refreshed)
- same fingerprint DISMISSED -> stays dismissed
- FIXED/AUTO_RESOLVED or new -> a new OPEN finding
- OPEN deterministic finding not seen this run -> AUTO_RESOLVED
  (LLM findings are never auto-resolved: model output varies between runs)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from anthropic import AsyncAnthropic
from sqlmodel import Session, select

from app.models.wiki import WikiOperation
from app.models.wiki_lint import LintFinding, LintFindingStatus, LintRun, LintRunStatus
from app.services.wiki_lint.checks import run_deterministic_checks
from app.services.wiki_lint.findings import DETERMINISTIC_KINDS, FindingDraft
from app.services.wiki_lint.llm_checks import run_llm_checks
from app.services.wiki_store import append_log

logger = logging.getLogger(__name__)
_STALE_RUN_AFTER = timedelta(minutes=30)


class LintAlreadyRunning(Exception):
    pass


def start_run(session: Session, trigger: str) -> LintRun:
    cutoff = datetime.utcnow() - _STALE_RUN_AFTER
    running = session.exec(select(LintRun).where(LintRun.status == LintRunStatus.RUNNING,
                                                 LintRun.started_at >= cutoff)).first()
    if running:
        raise LintAlreadyRunning(f"Lint run {running.id} is already in progress")
    run = LintRun(trigger=trigger)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def persist_findings(session: Session, run: LintRun, drafts: list[FindingDraft]) -> tuple[int, int, int]:
    new = 0
    seen_ids: set[int] = set()
    for draft in {d.fingerprint(): d for d in drafts}.values():
        fp = draft.fingerprint()
        latest = session.exec(select(LintFinding).where(LintFinding.fingerprint == fp)
                              .order_by(LintFinding.id.desc())).first()
        if latest and latest.status in (LintFindingStatus.OPEN, LintFindingStatus.DISMISSED):
            latest.last_seen_run_id = run.id
            if latest.status == LintFindingStatus.OPEN:
                latest.summary, latest.suggested_action = draft.summary, draft.suggested_action
            session.add(latest)
            seen_ids.add(latest.id)
            continue
        finding = LintFinding(
            fingerprint=fp, kind=draft.kind, domain=draft.domain, summary=draft.summary,
            suggested_action=draft.suggested_action, wiki_page_ids_json=json.dumps(list(draft.wiki_page_ids)),
            claim_ids_json=json.dumps(list(draft.claim_ids)), document_ids_json=json.dumps(list(draft.document_ids)),
            record_ids_json=json.dumps(list(draft.record_ids)), first_seen_run_id=run.id, last_seen_run_id=run.id)
        session.add(finding)
        session.flush()
        seen_ids.add(finding.id)
        new += 1
    auto = 0
    for f in session.exec(select(LintFinding).where(LintFinding.status == LintFindingStatus.OPEN)).all():
        if f.id not in seen_ids and f.kind in DETERMINISTIC_KINDS:
            f.status, f.resolved_at, f.resolved_by = LintFindingStatus.AUTO_RESOLVED, datetime.utcnow(), "lint"
            session.add(f)
            auto += 1
    session.commit()
    open_count = len(session.exec(select(LintFinding).where(LintFinding.status == LintFindingStatus.OPEN)).all())
    return new, open_count, auto


async def execute_run(session: Session, run_id: int, client: Optional[AsyncAnthropic] = None) -> LintRun:
    run = session.get(LintRun, run_id)
    try:
        drafts = run_deterministic_checks(session)
        llm_drafts, errors = await run_llm_checks(session, client)
        run.new_count, run.open_count, run.auto_resolved_count = persist_findings(session, run, drafts + llm_drafts)
        run.status = LintRunStatus.PARTIAL if errors else LintRunStatus.SUCCEEDED
        run.errors = "\n".join(errors) or None
    except Exception as exc:
        logger.exception("Lint run %s failed", run_id)
        session.rollback()
        run = session.get(LintRun, run_id)
        run.status, run.errors = LintRunStatus.FAILED, str(exc)
    run.finished_at = datetime.utcnow()
    session.add(run)
    session.commit()
    append_log(session, WikiOperation.LINT, f"Wiki check {run.status.value}: {run.new_count} new, "
                                            f"{run.open_count} open, {run.auto_resolved_count} auto-resolved")
    session.refresh(run)
    return run


async def run_lint(session: Session, trigger: str, client: Optional[AsyncAnthropic] = None) -> LintRun:
    run = start_run(session, trigger)
    return await execute_run(session, run.id, client)
