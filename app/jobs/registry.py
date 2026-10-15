"""The background jobs whose schedule is editable on the Hub (decision D15).

Each job: a label, a plain description, a default schedule, and an async runner that
returns a JobOutcome. Adding a job = adding an entry here; the scheduler, the screen and the
history pick it up. Jobs still on a fixed server timer are listed in FIXED_JOBS so the screen
can show them honestly (read-only) until they are moved over."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Optional

from sqlmodel import Session, select

from app.services.schedule import Spec


@dataclass(frozen=True)
class JobOutcome:
    outcome: str          # ok | partial | failed
    summary: str          # plain language: what happened, and why if it did not fully work


@dataclass(frozen=True)
class JobDef:
    key: str
    label: str
    description: str
    default: Spec
    run: Callable[[Session, str], Awaitable[JobOutcome]]
    legacy_last_start: Optional[Callable[[Session], Optional[datetime]]] = None


async def _run_wiki_lint(session: Session, trigger: str) -> JobOutcome:
    from app.models.wiki_lint import LintRunStatus
    from app.services.wiki_lint.runner import LintAlreadyRunning, run_lint
    try:
        run = await run_lint(session, trigger="timer" if trigger == "schedule" else "manual")
    except LintAlreadyRunning:
        return JobOutcome("failed", "Not started. A wiki check was already running, so this one would have collided with it. "
                                    "Wait for it to finish (it can take a few minutes), then run it again.")
    from app.problems import explain_lint_errors
    if run.status == LintRunStatus.FAILED:
        return JobOutcome("failed", explain_lint_errors(run.errors, whole_run=True))
    if run.status == LintRunStatus.PARTIAL:
        return JobOutcome("partial", f"Partly done: {run.open_count} open finding(s). " + explain_lint_errors(run.errors, whole_run=False))
    return JobOutcome("ok", f"Done: {run.new_count} new finding(s), {run.open_count} open in total.")


def _lint_legacy_last_start(session: Session) -> Optional[datetime]:
    """The fixed weekly timer's last run, so switching to the flexible schedule does not re-run it at once."""
    from app.models.wiki_lint import LintRun
    row = session.exec(select(LintRun).where(LintRun.trigger == "timer").order_by(LintRun.started_at.desc())).first()
    return row.started_at if row else None


JOBS: dict[str, JobDef] = {
    "wiki_lint": JobDef(
        key="wiki_lint", label="Wiki check",
        description="Looks through the household wiki for contradictions, out-of-date facts, gaps and loose ends, "
                    "and lists them for you to review. It never changes a fact by itself.",
        default=Spec(kind="weekly", weekday=6, time_of_day="04:30"),
        run=_run_wiki_lint, legacy_last_start=_lint_legacy_last_start),
}

# Still on fixed server timers (not yet adjustable here): (label, when, what it does)
FIXED_JOBS = [
    ("Bank sync", "3 times a day (07:30, 13:00, 18:30)", "Fetches new transactions from the linked bank accounts."),
    ("Mailbox check", "Every 10 minutes", "Picks up documents forwarded to the Hub's email address."),
    ("Statement reminder", "Every day at 09:00 (sends only when due)", "Reminds you on Telegram to upload a statement."),
]
