from pathlib import Path

import pytest

from app.jobs import wiki_lint as job
from app.models.wiki_lint import LintRun, LintRunStatus
from app.services.wiki_lint.runner import LintAlreadyRunning


@pytest.mark.asyncio
async def test_main_runs_timer_lint(engine, monkeypatch):
    seen = {}
    async def _run(session, trigger, client=None):
        seen["trigger"] = trigger
        return LintRun(trigger=trigger, status=LintRunStatus.SUCCEEDED)
    monkeypatch.setattr(job, "engine", engine)
    monkeypatch.setattr(job, "run_lint", _run)
    assert await job.main() == 0 and seen["trigger"] == "timer"


@pytest.mark.asyncio
async def test_main_exits_cleanly_when_already_running(engine, monkeypatch):
    async def _run(session, trigger, client=None):
        raise LintAlreadyRunning("busy")
    monkeypatch.setattr(job, "engine", engine)
    monkeypatch.setattr(job, "run_lint", _run)
    assert await job.main() == 0


def test_user_units_point_at_the_job():
    service = Path("deploy/systemd/home-hub-wikilint.service").read_text()
    timer = Path("deploy/systemd/home-hub-wikilint.timer").read_text()
    assert "-m app.jobs.wiki_lint" in service and "User=" not in service  # user unit: no User=
    assert "OnCalendar=Sun *-*-* 03:30:00" in timer and "Persistent=true" in timer
