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


def test_the_hubs_scheduler_units_replace_the_fixed_weekly_timer():
    service = Path("deploy/systemd/home-hub-scheduler.service").read_text()
    timer = Path("deploy/systemd/home-hub-scheduler.timer").read_text()
    assert "-m app.jobs.scheduler" in service and "User=" not in service     # user unit: no User=
    assert "OnUnitActiveSec=15min" in timer and "OnBootSec=" in timer
    # the old fixed timer is gone from the repo and retired on the server, so a check never runs twice
    assert not Path("deploy/systemd/home-hub-wikilint.timer").exists()
    installer = Path("deploy/install_user_units.sh").read_text()
    assert "home-hub-wikilint.timer" in installer.split("RETIRED_UNITS=")[1].splitlines()[0]
