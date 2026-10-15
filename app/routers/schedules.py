"""Scheduled jobs page: how often each background job runs, run it now, and its history (D15)."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.db import get_session, get_session_factory
from app.jobs.registry import FIXED_JOBS, JOBS
from app.jobs.scheduler import effective_spec, last_scheduled_start, run_job
from app.models.schedule import JobRun, JobSchedule
from app.services import schedule as sched
from app.templating import templates

router = APIRouter(prefix="/schedules", tags=["schedules"])


def _by(request: Request) -> Optional[str]:
    return getattr(request.state, "user_email", None)


@router.get("")
def page(request: Request, session: Session = Depends(get_session), problem: str = "", saved: str = ""):
    now = datetime.now(timezone.utc)
    rows = []
    for job in JOBS.values():
        spec = effective_spec(session, job)
        last_start = last_scheduled_start(session, job)
        nxt = sched.next_run(spec, last_start, now)
        latest = session.exec(select(JobRun).where(JobRun.job == job.key).order_by(JobRun.started_at.desc())).first()
        rows.append({"job": job, "spec": spec, "text": sched.describe(spec), "latest": latest,
                     "next": nxt.astimezone(timezone.utc).replace(tzinfo=None) if nxt else None, "due_now": nxt is not None and nxt <= now,
                     "custom": session.get(JobSchedule, job.key) is not None})
    history = session.exec(select(JobRun).order_by(JobRun.started_at.desc()).limit(15)).all()
    return templates.TemplateResponse(request, "schedules/page.html", {
        "rows": rows, "fixed": FIXED_JOBS, "history": history, "kinds": sched.KINDS, "weekdays": sched.WEEKDAYS,
        "labels": {j.key: j.label for j in JOBS.values()}, "problem": problem, "saved": saved,
    })


@router.post("/{job_key}")
def save(request: Request, job_key: str, kind: str = Form("weekly"), every_n: str = Form("1"),
         weekday: str = Form("0"), day_of_month: str = Form("1"), time_of_day: str = Form("04:30"),
         enabled: Optional[str] = Form(None), session: Session = Depends(get_session)):
    from urllib.parse import urlencode
    if job_key not in JOBS:
        return RedirectResponse("/schedules", status_code=303)
    try:
        spec = sched.validate(sched.Spec(kind, int(every_n or 1), int(weekday or 0), int(day_of_month or 1),
                                         time_of_day.strip(), enabled is not None))
    except (sched.InvalidSchedule, ValueError) as exc:
        msg = str(exc) if isinstance(exc, sched.InvalidSchedule) else "Enter whole numbers for the day and the count."
        return RedirectResponse("/schedules?" + urlencode({"problem": f"Not saved. {msg} The schedule was left as it was."}), status_code=303)
    session.merge(JobSchedule(job=job_key, kind=spec.kind, every_n=spec.every_n, weekday=spec.weekday,
                              day_of_month=spec.day_of_month, time_of_day=spec.time_of_day, enabled=spec.enabled,
                              updated_by=_by(request), updated_at=datetime.utcnow()))
    session.commit()
    return RedirectResponse("/schedules?" + urlencode({"saved": job_key}), status_code=303)


async def _run_in_background(session_factory, job_key: str) -> None:
    with session_factory() as session:
        await run_job(session, job_key, "manual")


@router.post("/{job_key}/run")
async def run_now(job_key: str, background: BackgroundTasks, session_factory=Depends(get_session_factory)):
    if job_key in JOBS:
        background.add_task(_run_in_background, session_factory, job_key)
    return RedirectResponse("/schedules", status_code=303)
