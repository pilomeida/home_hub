"""The privacy fail-closed self-test (app/privacy/selftest.py): it passes on the real code, fails when a tripwire is
cut, records a three-part problem, and never calls a model."""

import pytest
from sqlmodel import select

from app.jobs import registry
from app.models.schedule import ProblemEntry
from app.privacy import reach as reach_module, selftest, tiers
from app.privacy.tiers import Tier


def test_every_tripwire_holds_on_the_real_code():
    assert selftest.run_selftest() == []


def test_the_test_uses_real_types_and_leaves_the_high_route_as_it_found_it():
    from app.privacy import settings as ps
    known = {(i.category, i.paper_kind) for i in ps.known_types()}
    assert {selftest.OPEN_TYPE, selftest.HIGH_TYPE, selftest.UNSET_TYPE, selftest.FORCED_HIGH_TYPE} <= known
    selftest.run_selftest()
    assert tiers.POLICIES[Tier.HIGH].route_available is True


def test_it_fails_when_a_document_with_no_level_becomes_reachable(monkeypatch):
    real = reach_module.Reach.includes_type
    monkeypatch.setattr(reach_module.Reach, "includes_type", lambda self, c, k: True if k == "statement" else real(self, c, k))
    failures = selftest.run_selftest()
    assert any("no level" in f for f in failures)


def test_it_fails_when_the_first_call_may_see_a_high_document(monkeypatch):
    monkeypatch.setattr(reach_module.Reach, "without_private", lambda self: self)
    assert any("first call" in f for f in selftest.run_selftest())


def test_it_fails_when_a_missing_high_route_no_longer_blocks(monkeypatch):
    monkeypatch.setattr(tiers, "policy_for", lambda tier: tiers.POLICIES[Tier.HIGH])
    assert any("route" in f for f in selftest.run_selftest())


@pytest.mark.asyncio
async def test_the_job_passes_quietly_and_clears_an_old_problem(session):
    session.add(ProblemEntry(area=selftest.AREA, what="The privacy self-test failed.", why="x", next_step="y", ref=selftest.REF))
    session.commit()
    outcome = await selftest.run_privacy_selftest(session, "schedule", runner=selftest.run_selftest)
    assert outcome.outcome == "ok" and "Nothing was sent" in outcome.summary
    assert session.exec(select(ProblemEntry).where(ProblemEntry.resolved_at == None)).all() == []   # noqa: E711


@pytest.mark.asyncio
async def test_a_failure_is_recorded_on_the_problems_page_with_what_why_and_next_step(session, monkeypatch):
    monkeypatch.setattr(reach_module.Reach, "without_private", lambda self: self)
    outcome = await selftest.run_privacy_selftest(session, "schedule", runner=selftest.run_selftest)
    assert outcome.outcome == "failed"
    row = session.exec(select(ProblemEntry).where(ProblemEntry.ref == selftest.REF)).one()
    assert row.area == "Privacy" and row.what == "The privacy self-test failed."
    assert "first call" in row.why and row.next_step.startswith("Tell Pedro") and row.link == "/problems"


@pytest.mark.asyncio
async def test_a_test_that_cannot_run_is_a_failure_not_a_pass(session, monkeypatch):
    def boom():
        raise RuntimeError("scratch database broke")
    monkeypatch.setattr(selftest, "_scratch", boom)
    outcome = await selftest.run_privacy_selftest(session, "schedule", runner=selftest.run_selftest)
    assert outcome.outcome == "failed" and "could not run" in outcome.summary


def test_it_runs_nightly_on_the_hubs_scheduler_and_calls_no_model():
    job = registry.JOBS["privacy_selftest"]
    assert job.run is not None and job.default.kind == "daily"
    source = open(selftest.__file__).read()
    assert "GatewayClient" not in source and ".run(" not in source


@pytest.mark.asyncio
async def test_the_job_runs_the_checks_in_a_fresh_process_by_default(session):
    """The default runner must not share the web app's global tier policies; it reads the child's JSON answer."""
    outcome = await selftest.run_privacy_selftest(session, "manual")
    assert outcome.outcome == "ok" and "Nothing was sent" in outcome.summary


@pytest.mark.asyncio
async def test_a_child_that_cannot_run_is_a_failure_not_a_pass(session, monkeypatch):
    import subprocess
    def broken(*a, **k):
        raise subprocess.CalledProcessError(1, "selftest")
    monkeypatch.setattr(selftest.subprocess, "check_output", broken)
    outcome = await selftest.run_privacy_selftest(session, "manual")
    assert outcome.outcome == "failed" and "could not run" in outcome.summary
