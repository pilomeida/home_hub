"""The gear's page, new look (PLAN step 10f): a card per setting with what it says today (open problems, the bank
connections, the privacy levels, the scheduled jobs, what the Hub knows), each opening its own page, unchanged.

Presentation only: every figure comes from the code behind those pages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, func, select

from app.models.wiki import ClaimStatus, WikiClaim
from app.problems import open_problems
from app.privacy import settings as ps
from app.privacy.tiers import Tier

LEVELS = [(Tier.LOW, "Low"), (Tier.MID, "Mid"), (Tier.HIGH, "High")]


@dataclass
class JobLine:
    label: str
    when: str
    outcome: Optional[str]          # the latest run's outcome, None before the first run


@dataclass
class SettingsPage:
    problems: list
    problems_open: int
    banks: list[dict]
    levels: list[tuple[str, int]]   # ("Low", 12), …, ("No level yet", 2)
    enforcing: bool
    jobs: list[JobLine]
    facts: int
    facts_new: int                  # added in the last 30 days


def settings_page(session: Session, now: Optional[datetime] = None) -> SettingsPage:
    from app.jobs.registry import JOBS
    from app.jobs.scheduler import effective_spec
    from app.models.schedule import JobRun
    from app.services.bankapi.notices import connection_rows
    from app.services import schedule as sched
    now = now or datetime.now(timezone.utc)
    problems = open_problems(session, limit=100)
    rows = ps.type_rows(session)
    levels = [(label, sum(1 for r in rows if r.tier == tier)) for tier, label in LEVELS]
    unset = sum(1 for r in rows if r.tier is None)
    if unset:
        levels.append(("No level yet", unset))
    jobs = []
    for job in JOBS.values():
        latest = session.exec(select(JobRun).where(JobRun.job == job.key).order_by(JobRun.started_at.desc())).first()
        jobs.append(JobLine(job.label, sched.describe(effective_spec(session, job)), latest.outcome if latest else None))
    active = WikiClaim.status == ClaimStatus.ACTIVE
    facts = session.exec(select(func.count()).select_from(WikiClaim).where(active)).one()
    since = now.replace(tzinfo=None) - timedelta(days=30)
    new = session.exec(select(func.count()).select_from(WikiClaim).where(active, WikiClaim.created_at >= since)).one()
    return SettingsPage(problems[:3], len(problems), connection_rows(session, now.replace(tzinfo=None)), levels,
                        ps.enforcing(session), jobs, facts, new)


__all__ = ["SettingsPage", "JobLine", "settings_page"]
