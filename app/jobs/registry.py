"""The background jobs whose schedule is editable on the Hub (decision D15).

Each job: a label, a plain description, a default schedule, and an async runner that
returns a JobOutcome. Adding a job = adding an entry here; the scheduler, the screen and the
history pick it up. FIXED_JOBS lists jobs still on a fixed server timer (none now); the screen
shows that section only when the list is not empty."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Optional

from sqlmodel import Session, select

from app.jobs.outcome import JobOutcome
from app.jobs.runners import (
    BANK_LIMIT_REASON, bank_legacy_last_start, run_bank_sync, run_check_nudge, run_mailbox_check, run_statement_reminder,
)
from app.services.schedule import Spec

__all__ = ["JobDef", "JobOutcome", "JOBS", "FIXED_JOBS"]


@dataclass(frozen=True)
class JobDef:
    key: str
    label: str
    description: str
    default: Spec
    run: Callable[[Session, str], Awaitable[JobOutcome]]
    legacy_last_start: Optional[Callable[[Session], Optional[datetime]]] = None
    # The most times a day this job may start, and why. A schedule that could exceed it is refused when saved.
    max_runs_per_day: Optional[int] = None
    max_runs_reason: str = ""
    # A job that runs every few minutes: its routine "ok" runs stay out of the history table and are
    # cleared after a couple of days (they would bury everything else); failures and manual runs are always kept.
    quiet_when_ok: bool = False


async def _run_wiki_lint(session: Session, trigger: str) -> JobOutcome:
    from app.models.wiki_lint import LintRunStatus
    from app.services.wiki_lint.runner import LintAlreadyRunning, run_lint
    try:
        run = await run_lint(session, trigger="timer" if trigger == "schedule" else "manual")
    except LintAlreadyRunning:
        return JobOutcome("failed", "Not started. A wiki check was already running, so this one would have collided with it. "
                                    "Wait for it to finish (it can take a few minutes), then run it again.")
    from app.privacy.reach import LeftOut
    from app.problems import explain_lint_errors
    left_out = LeftOut.from_json(run.left_out_json).problem("check")
    skipped = f" {left_out.text}" if left_out is not None else ""     # the privacy levels left some sources out
    if run.status == LintRunStatus.FAILED:
        return JobOutcome("failed", explain_lint_errors(run.errors, whole_run=True) + skipped)
    if run.status == LintRunStatus.PARTIAL:
        return JobOutcome("partial", f"Partly done: {run.open_count} open finding(s). "
                                     + explain_lint_errors(run.errors, whole_run=False) + skipped)
    done = f"Done: {run.new_count} new finding(s), {run.open_count} open in total."
    # A check that ran but not over everything must not look complete.
    return JobOutcome("partial" if left_out is not None else "ok", done + skipped)


def _lint_legacy_last_start(session: Session) -> Optional[datetime]:
    """The fixed weekly timer's last run, so switching to the flexible schedule does not re-run it at once."""
    from app.models.wiki_lint import LintRun
    row = session.exec(select(LintRun).where(LintRun.trigger == "timer").order_by(LintRun.started_at.desc())).first()
    return row.started_at if row else None


# The order is the order the scheduler runs due jobs in (one after another): quick, frequent jobs first, so a
# long bank sync or wiki check never holds up the mailbox check or the reminder that were due at the same moment.
JOBS: dict[str, JobDef] = {
    "mailbox_check": JobDef(
        key="mailbox_check", label="Mailbox check",
        description="Picks up documents forwarded to the Hub's email address and puts them in the Inbox.",
        default=Spec(kind="every_n_minutes", every_n=10),
        run=run_mailbox_check, quiet_when_ok=True),
    "statement_reminder": JobDef(
        key="statement_reminder", label="Statement reminder",
        description="Checks once a day whether it is time to download the latest bank statements, and if so reminds you on "
                    "Telegram (once per cycle). On every other day it does nothing.",
        default=Spec(kind="daily", time_of_day="09:00"),
        run=run_statement_reminder),
    "check_nudge": JobDef(
        key="check_nudge", label="Things to check",
        description="Once a week, if bank statement lines are waiting on the To check list, sends one Telegram message saying how many "
                    "and linking to the list. When nothing is waiting it sends nothing.",
        default=Spec(kind="weekly", weekday=0, time_of_day="09:00"),
        run=run_check_nudge),
    "bank_sync": JobDef(
        key="bank_sync", label="Bank sync",
        description="Fetches new transactions from the linked bank accounts and files them. The bank allows only 4 requests per "
                    "account per day, and the “Sync now” button on the bank page uses the same allowance, so leave one spare.",
        default=Spec(kind="times_per_day", time_of_day="07:30,13:00,18:30"),
        run=run_bank_sync, legacy_last_start=bank_legacy_last_start,
        max_runs_per_day=4, max_runs_reason=BANK_LIMIT_REASON),
    "wiki_lint": JobDef(
        key="wiki_lint", label="Wiki check",
        description="Looks through the household wiki for contradictions, out-of-date facts, gaps and loose ends, "
                    "and lists them for you to review. It never changes a fact by itself.",
        default=Spec(kind="weekly", weekday=6, time_of_day="04:30"),
        run=_run_wiki_lint, legacy_last_start=_lint_legacy_last_start),
}

# Jobs still on fixed server timers (not adjustable here): (label, when, what it does). Empty since the
# bank sync, mailbox check and statement reminder moved onto the Hub's scheduler.
FIXED_JOBS: list[tuple[str, str, str]] = []
