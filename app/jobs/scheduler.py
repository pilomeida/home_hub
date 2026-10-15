"""The one clock for jobs whose schedule is set on the Hub (decision D15).

A systemd user timer starts this every 5 minutes (deploy/systemd/home-hub-scheduler.timer);
it runs whichever jobs are due according to the schedules saved on the Scheduled jobs
page (/schedules), and exits. Usage: python -m app.jobs.scheduler

One slow job (a long bank sync) cannot be started twice: the next timer tick finds the job's "running"
record and leaves it alone, and systemd does not start a second copy of this one-shot service while the
first is still going."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from app.jobs.registry import JOBS, JobDef, JobOutcome
from app.models.schedule import JobRun, JobSchedule
from app.problems import Problem, explain, record_problem, resolve_for
from app.services import schedule as sched

logger = logging.getLogger("scheduler")
_STALE_AFTER = timedelta(minutes=45)           # a "running" record older than this is a crashed run, not a live one
_QUIET_KEEP = timedelta(days=2)                # how long the routine runs of a quiet_when_ok job are kept


def effective_spec(session: Session, job: JobDef) -> sched.Spec:
    row = session.get(JobSchedule, job.key)
    return sched.spec_of(row) if row is not None else job.default


def last_scheduled_start(session: Session, job: JobDef) -> Optional[datetime]:
    """Start of the last run that counts toward the cycle: scheduled runs only (a manual run never moves it)."""
    row = session.exec(select(JobRun).where(JobRun.job == job.key, JobRun.trigger == "schedule",
                                            JobRun.outcome != "skipped").order_by(JobRun.started_at.desc())).first()
    if row is not None:
        return row.started_at.replace(tzinfo=timezone.utc)
    legacy = job.legacy_last_start(session) if job.legacy_last_start else None
    return legacy.replace(tzinfo=timezone.utc) if legacy else None


def _running(session: Session, job_key: str) -> Optional[JobRun]:
    cutoff = datetime.utcnow() - _STALE_AFTER
    return session.exec(select(JobRun).where(JobRun.job == job_key, JobRun.outcome == "running",
                                             JobRun.started_at >= cutoff)).first()


def _forget_old_quiet_runs(session: Session, job_key: str) -> None:
    """A job that runs every few minutes would otherwise add hundreds of rows a day. Old routine scheduled
    runs are dropped; failures, partial runs and manual runs stay, and the newest scheduled run is far younger
    than the cutoff, so the cycle's anchor is never lost."""
    cutoff = datetime.utcnow() - _QUIET_KEEP
    for old in session.exec(select(JobRun).where(JobRun.job == job_key, JobRun.trigger == "schedule",
                                                 JobRun.outcome == "ok", JobRun.started_at < cutoff)).all():
        session.delete(old)
    session.commit()


async def run_job(session: Session, job_key: str, trigger: str) -> JobRun:
    """Run one job now and record the result. Never raises: a failure becomes a plain-language
    message in the run history and in the Problems log."""
    job = JOBS[job_key]
    if _running(session, job_key) is not None:
        run = JobRun(job=job_key, trigger=trigger, outcome="skipped", finished_at=datetime.utcnow(),
                     summary="Not started. This job is already running, and two at once would collide. "
                             "Wait for the current run to finish, then run it again.")
        session.add(run); session.commit(); session.refresh(run)
        return run
    run = JobRun(job=job_key, trigger=trigger)
    session.add(run); session.commit(); session.refresh(run)
    try:
        outcome = await job.run(session, trigger)
    except Exception as exc:                      # a job must never take the scheduler down
        logger.exception("job %s failed", job_key)
        problem = explain("Not done.", f"{type(exc).__name__}: {exc}")
        outcome = JobOutcome("failed", f"{problem.what} {problem.why} {problem.next_step}")
    session.refresh(run)
    run.outcome, run.summary, run.finished_at = outcome.outcome, outcome.summary, datetime.utcnow()
    session.add(run); session.commit(); session.refresh(run)
    if job.quiet_when_ok:
        _forget_old_quiet_runs(session, job_key)
    ref = f"job:{job_key}"
    if outcome.outcome in ("failed", "partial"):
        what = f"{job.label} didn\u2019t finish." if outcome.outcome == "failed" else f"{job.label} only partly worked."
        record_problem(session, area="Scheduled jobs",
                       problem=Problem(what, outcome.summary, "See the run history on the Scheduled jobs page.", "/schedules"),
                       ref=ref)
    else:
        resolve_for(session, ref)
    return run


async def run_due(session: Session, now: Optional[datetime] = None) -> list[JobRun]:
    now = now or datetime.now(timezone.utc)
    ran = []
    for job in JOBS.values():
        spec = effective_spec(session, job)
        if sched.is_due(spec, last_scheduled_start(session, job), now):
            if _running(session, job.key) is not None:     # still going from an earlier check: leave it alone
                logger.info("%s is still running; not starting it again", job.key)
                continue
            ran.append(await run_job(session, job.key, "schedule"))
    return ran


async def main() -> int:
    logging.basicConfig(level=logging.INFO)
    from app.db import engine
    with Session(engine) as session:
        runs = await run_due(session)
        # Read the results while the session is still open: the rows are detached once it closes.
        results = [(r.job, r.outcome, r.summary) for r in runs]
    for job, outcome, summary in results:
        logger.info("%s: %s — %s", job, outcome, summary)
    return 1 if any(outcome == "failed" for _, outcome, _ in results) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
