"""Notices on the phone (finding F15, work-list C6): the one place that decides what reaches a person by Telegram.

Each run works out what currently needs a person, from what the Hub already stores (nothing has to remember to
"send a notice"), and compares it with what was already told (the `notices` table):
- new             -> sent, all in one message;
- still open      -> reminded once after REMIND_AFTER, only for the kinds in REMINDS;
- no longer holds -> closed quietly.
Between QUIET_FROM and QUIET_UNTIL (Lisbon time) nothing is sent; it waits for the morning.

The four kinds (Pedro, 2026-11-03): a bank's access has expired; a scheduled job keeps failing; a bill is due within
BILL_DAYS days; a new loan red flag. Lines carry administrative facts only (decision D21) and point to the Hub."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Optional

from sqlmodel import Session, select

from app.config import settings
from app.models.bank import BankConnectionStatus
from app.models.debt import Debt
from app.models.notice import Notice
from app.models.position import LoanAlert
from app.models.schedule import JobRun
from app.models.todo import Todo
from app.services.bankapi.connect import connection_label, latest_connections
from app.services.schedule import LISBON

BANK_EXPIRED, JOB_FAILING, BILL_DUE, LOAN_FLAG = "bank_expired", "job_failing", "bill_due", "loan_flag"
BILL_DAYS = 3
FAILED_RUNS = 2                         # "keeps failing": this many scheduled runs in a row did not work
REMIND_AFTER = timedelta(days=3)
REMINDS = {BANK_EXPIRED, JOB_FAILING}   # a bill and a loan flag are told once
QUIET_FROM, QUIET_UNTIL = time(22, 0), time(8, 0)


@dataclass(frozen=True)
class Found:
    kind: str
    ref: str
    text: str
    link: str


def _bank_expired(session: Session) -> list[Found]:
    return [Found(BANK_EXPIRED, f"connection:{c.id}",
                  f"{connection_label(c)}: the Hub's access to this bank has expired, so nothing syncs until it is renewed.",
                  "/financials/bank/")
            for c in latest_connections(session).values() if c.status == BankConnectionStatus.EXPIRED]


def _utc(now: datetime) -> datetime:
    """The naive UTC time the Hub stores (datetime.utcnow)."""
    return now if now.tzinfo is None else now.astimezone(timezone.utc).replace(tzinfo=None)


def _jobs_failing(session: Session) -> list[Found]:
    from app.jobs.registry import JOBS            # the registry imports this module's runner
    found = []
    for key, label in ((k, j.label) for k, j in JOBS.items()):
        runs = session.exec(select(JobRun).where(JobRun.job == key, JobRun.trigger == "schedule",
                                                 JobRun.outcome.in_(("ok", "partial", "failed")))
                            .order_by(JobRun.started_at.desc()).limit(FAILED_RUNS)).all()
        if len(runs) == FAILED_RUNS and all(r.outcome in ("partial", "failed") for r in runs):
            found.append(Found(JOB_FAILING, f"job:{key}",
                               f"{label} has not worked the last {FAILED_RUNS} times it ran.", "/schedules"))
    return found


def _bills_due(session: Session, today: date) -> list[Found]:
    todos = session.exec(select(Todo).where(Todo.done == False, Todo.transaction_id.is_not(None),  # noqa: E712
                                            Todo.due_date >= today, Todo.due_date <= today + timedelta(days=BILL_DAYS))).all()
    return [Found(BILL_DUE, f"todo:{t.id}", f"Bill due {t.due_date.isoformat()}: {t.title.removeprefix('Pay ')}.", "/todos")
            for t in todos]


def _loan_flags(session: Session) -> list[Found]:
    found = []
    for alert in session.exec(select(LoanAlert).where(LoanAlert.acknowledged == False)).all():  # noqa: E712
        debt = session.get(Debt, alert.debt_id)
        name = (debt.name if debt is not None and debt.name else "a loan")
        found.append(Found(LOAN_FLAG, f"loan-alert:{alert.id}", f"New red flag on {name}: look at it on the Loans page.",
                           f"/financials/loans/{alert.debt_id}"))
    return found


def current(session: Session, today: date) -> list[Found]:
    return _bank_expired(session) + _jobs_failing(session) + _bills_due(session, today) + _loan_flags(session)


def quiet(now_lisbon: datetime) -> bool:
    t = now_lisbon.time()
    return t >= QUIET_FROM or t < QUIET_UNTIL


@dataclass
class Plan:
    new: list[Notice]
    reminders: list[Notice]
    closed: int


def refresh(session: Session, now: datetime) -> Plan:
    """Bring the table in line with what holds now. Returns what is due to be sent (not yet marked sent)."""
    today = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(LISBON).date()
    naive = _utc(now)
    found = {(f.kind, f.ref): f for f in current(session, today)}
    rows = {(n.kind, n.ref): n for n in session.exec(select(Notice)).all()}
    closed = 0
    for key, row in rows.items():
        if key not in found and row.resolved_at is None:
            row.resolved_at = naive
            session.add(row)
            closed += 1
    for key, f in found.items():
        row = rows.get(key)
        if row is None:
            row = Notice(kind=f.kind, ref=f.ref, text=f.text, link=f.link, first_seen=naive)
        elif row.resolved_at is not None:            # it came back: a new episode, told again
            row.resolved_at, row.sent_at, row.reminded_at, row.first_seen = None, None, None, naive
        row.text, row.link = f.text, f.link
        session.add(row)
        rows[key] = row
    session.commit()
    open_rows = [r for r in rows.values() if r.resolved_at is None]
    new = [r for r in open_rows if r.sent_at is None]
    reminders = [r for r in open_rows if r.sent_at is not None and r.reminded_at is None and r.kind in REMINDS
                 and naive - r.sent_at >= REMIND_AFTER]
    return Plan(new, reminders, closed)


def message(plan: Plan) -> str:
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    lines = [f"• {n.text} {base}{n.link}" for n in plan.new]
    lines += [f"• Still open: {n.text} {base}{n.link}" for n in plan.reminders]
    head = "The Hub needs you:" if len(lines) > 1 else "The Hub needs you."
    return "\n".join([head, *lines])


def mark_sent(session: Session, plan: Plan, now: datetime) -> None:
    naive = _utc(now)
    for n in plan.new:
        n.sent_at = naive
        session.add(n)
    for n in plan.reminders:
        n.reminded_at = naive
        session.add(n)
    session.commit()


Sender = Callable[[str], None]


def deliver(session: Session, now: datetime, send: Sender) -> tuple[Plan, bool]:
    """Refresh, then send what is due unless it is quiet time. (plan, sent?). A failed send raises and nothing is
    marked, so the next run tries again."""
    plan = refresh(session, now)
    if not (plan.new or plan.reminders) or quiet((now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(LISBON)):
        return plan, False
    send(message(plan))
    mark_sent(session, plan, now)
    return plan, True


def recipient() -> Optional[int]:
    from app.jobs.statement_reminder import find_recipient
    return find_recipient(settings.telegram_allowed_users, settings.HUB_REMINDER_TELEGRAM_NAME)
