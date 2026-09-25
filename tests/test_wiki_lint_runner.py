import json

import pytest
from sqlmodel import select

from app.models.wiki import WikiLogEntry, WikiOperation
from app.models.wiki_lint import LintFinding, LintFindingKind, LintFindingStatus, LintRun, LintRunStatus
from app.services.wiki_lint import runner
from app.services.wiki_lint.findings import FindingDraft

ORPHAN = FindingDraft(LintFindingKind.ORPHAN_PAGE, "orphan", wiki_page_ids=(1,))
CONTRA = FindingDraft(LintFindingKind.CONTRADICTION, "contra", wiki_page_ids=(2,))


def _run(session):
    run = LintRun(trigger="manual")
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_new_then_seen_again_then_auto_resolved(session):
    assert runner.persist_findings(session, _run(session), [ORPHAN, CONTRA]) == (2, 2, 0)
    assert runner.persist_findings(session, _run(session), [ORPHAN, CONTRA]) == (0, 2, 0)
    assert runner.persist_findings(session, _run(session), []) == (0, 1, 1)
    statuses = {f.kind: f.status for f in session.exec(select(LintFinding)).all()}
    assert statuses[LintFindingKind.ORPHAN_PAGE] == LintFindingStatus.AUTO_RESOLVED
    assert statuses[LintFindingKind.CONTRADICTION] == LintFindingStatus.OPEN


def test_dismissed_stays_dismissed_and_fixed_reopens(session):
    runner.persist_findings(session, _run(session), [ORPHAN, CONTRA])
    for f in session.exec(select(LintFinding)).all():
        f.status = LintFindingStatus.DISMISSED if f.kind == LintFindingKind.ORPHAN_PAGE else LintFindingStatus.FIXED
        session.add(f)
    session.commit()
    new, _, _ = runner.persist_findings(session, _run(session), [ORPHAN, CONTRA])
    assert new == 1
    assert len(session.exec(select(LintFinding).where(LintFinding.kind == LintFindingKind.ORPHAN_PAGE)).all()) == 1


def test_start_run_refuses_concurrent(session):
    runner.start_run(session, "manual")
    with pytest.raises(runner.LintAlreadyRunning):
        runner.start_run(session, "timer")


@pytest.mark.asyncio
async def test_run_lint_partial_on_llm_errors_and_logs(session, monkeypatch):
    monkeypatch.setattr(runner, "run_deterministic_checks", lambda s: [ORPHAN])
    async def _llm(session, client=None):
        return [], ["House: boom"]
    monkeypatch.setattr(runner, "run_llm_checks", _llm)
    run = await runner.run_lint(session, "manual")
    assert run.status == LintRunStatus.PARTIAL and "boom" in run.errors and run.new_count == 1
    assert session.exec(select(WikiLogEntry).where(WikiLogEntry.operation == WikiOperation.LINT)).first()
    assert json.loads(session.exec(select(LintFinding)).one().wiki_page_ids_json) == [1]
