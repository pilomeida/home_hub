"""Phone notices (work-list C6): the four kinds Pedro chose on 2026-11-03, each told once, reminded at most once,
closed when it no longer holds, and held overnight. Real models in a test database; only the Telegram send is
replaced (the network edge)."""

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlmodel import select

from app.config import settings
from app.jobs import registry, runners, scheduler
from app.jobs import statement_reminder as reminder_job
from app.models.bank import BankConnection, BankConnectionStatus
from app.models.debt import Debt, DebtKind
from app.models.document import Document, DocumentSource
from app.models.notice import Notice
from app.models.position import LoanAlert
from app.models.schedule import JobRun
from app.models.todo import Todo
from app.models.transaction import Category, Transaction
from app.services import notices
from app.services.schedule import Spec

# 10:00 in Lisbon (UTC+0 in November): inside sending hours
MORNING = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)
TODAY = MORNING.date()


class Phone:
    def __init__(self):
        self.messages = []

    def __call__(self, text):
        self.messages.append(text)


def _expired_bank(session, owner="Rute"):
    conn = BankConnection(bank_name="Santander Totta", country="PT", state=f"s-{owner}", owner=owner,
                          status=BankConnectionStatus.EXPIRED)
    session.add(conn); session.commit(); session.refresh(conn)
    return conn


def _bill_todo(session, due: date, title="Pay EDP — 87.32 EUR"):
    doc = Document(filename="b.pdf", file_path="/tmp/b.pdf", content_hash=f"h-{due}-{title}", source=DocumentSource.MANUAL)
    session.add(doc); session.commit(); session.refresh(doc)
    t = Transaction(document_id=doc.id, description="EDP", category=Category.ELECTRICITY, amount=87.32, currency="EUR", due_date=due)
    session.add(t); session.commit(); session.refresh(t)
    todo = Todo(title=title, due_date=due, transaction_id=t.id)
    session.add(todo); session.commit(); session.refresh(todo)
    return todo


def _run(session, job, outcome, day):
    session.add(JobRun(job=job, trigger="schedule", outcome=outcome, started_at=datetime(2026, 11, day, 3, 0)))
    session.commit()


def test_an_expired_bank_is_told_once_and_closed_when_renewed(session):
    conn = _expired_bank(session)
    phone = Phone()
    _, sent = notices.deliver(session, MORNING, phone)
    assert sent and len(phone.messages) == 1
    assert "Santander Totta (Rute): the Hub's access to this bank has expired" in phone.messages[0]
    assert "https://hub.cdafamily.casa/financials/bank/" in phone.messages[0]

    _, sent = notices.deliver(session, MORNING + timedelta(minutes=15), phone)     # the next run: nothing new
    assert not sent and len(phone.messages) == 1

    conn.status = BankConnectionStatus.ACTIVE
    session.add(conn); session.commit()
    plan, _ = notices.deliver(session, MORNING + timedelta(minutes=30), phone)
    assert plan.closed == 1 and session.exec(select(Notice)).one().resolved_at is not None


def test_a_bank_still_expired_after_three_days_is_reminded_once(session):
    _expired_bank(session)
    phone = Phone()
    notices.deliver(session, MORNING, phone)
    notices.deliver(session, MORNING + timedelta(days=2), phone)
    assert len(phone.messages) == 1
    notices.deliver(session, MORNING + timedelta(days=3), phone)
    assert len(phone.messages) == 2 and "Still open:" in phone.messages[1]
    notices.deliver(session, MORNING + timedelta(days=9), phone)
    assert len(phone.messages) == 2                                                  # at most one reminder


def test_a_job_that_failed_twice_in_a_row_is_told_and_one_success_closes_it(session):
    _run(session, "backup", "ok", 1)
    _run(session, "backup", "failed", 2)
    phone = Phone()
    notices.deliver(session, MORNING, phone)
    assert phone.messages == []                                                      # once is not "keeps failing"
    _run(session, "backup", "partial", 3)
    notices.deliver(session, MORNING, phone)
    assert len(phone.messages) == 1 and "Backup has not worked the last 2 times it ran." in phone.messages[0]
    _run(session, "backup", "ok", 4)
    plan, _ = notices.deliver(session, MORNING + timedelta(minutes=15), phone)
    assert plan.closed == 1 and len(phone.messages) == 1


def test_a_bill_due_within_three_days_is_told_once_and_a_later_one_waits(session):
    _bill_todo(session, TODAY + timedelta(days=2))
    later = _bill_todo(session, TODAY + timedelta(days=6), title="Pay Vodafone — 40.00 EUR")
    phone = Phone()
    notices.deliver(session, MORNING, phone)
    assert len(phone.messages) == 1
    assert f"Bill due {(TODAY + timedelta(days=2)).isoformat()}: EDP — 87.32 EUR." in phone.messages[0]
    assert "Vodafone" not in phone.messages[0]

    notices.deliver(session, MORNING + timedelta(days=3, minutes=1), phone)          # now Vodafone is 3 days away
    assert len(phone.messages) == 2 and "Vodafone" in phone.messages[1] and "EDP" not in phone.messages[1]
    later.done = True
    session.add(later); session.commit()
    plan, _ = notices.deliver(session, MORNING + timedelta(days=3, minutes=30), phone)
    assert plan.closed >= 1


def test_a_new_loan_red_flag_is_told_by_the_loan_name_only(session):
    debt = Debt(kind=DebtKind.FORMAL, original_amount=100000.0, current_balance=90000, name="Mafra house – acquisition")
    session.add(debt); session.commit(); session.refresh(debt)
    alert = LoanAlert(debt_id=debt.id, kind="interest_only", ref="inst:16", message="capital 0.00, interest 312.40",
                      detected_on=TODAY)
    session.add(alert); session.commit()
    phone = Phone()
    notices.deliver(session, MORNING, phone)
    assert len(phone.messages) == 1
    assert "New red flag on Mafra house – acquisition" in phone.messages[0]
    assert "312.40" not in phone.messages[0]                                        # the detail stays in the Hub
    alert.acknowledged = True
    session.add(alert); session.commit()
    plan, _ = notices.deliver(session, MORNING + timedelta(hours=1), phone)
    assert plan.closed == 1


def test_several_things_go_in_one_message(session):
    _expired_bank(session)
    _bill_todo(session, TODAY + timedelta(days=1))
    phone = Phone()
    notices.deliver(session, MORNING, phone)
    assert len(phone.messages) == 1
    assert phone.messages[0].startswith("The Hub needs you:") and phone.messages[0].count("\n• ") == 2


def test_nothing_is_sent_at_night_and_it_waits_for_the_morning(session):
    _expired_bank(session)
    phone = Phone()
    night = datetime(2026, 11, 3, 23, 30, tzinfo=timezone.utc)
    plan, sent = notices.deliver(session, night, phone)
    assert not sent and phone.messages == [] and len(plan.new) == 1
    _, sent = notices.deliver(session, datetime(2026, 11, 4, 8, 5, tzinfo=timezone.utc), phone)
    assert sent and len(phone.messages) == 1


def test_a_failed_send_marks_nothing_so_the_next_run_tries_again(session):
    _expired_bank(session)

    def broken(text):
        raise RuntimeError("synthetic")
    with pytest.raises(RuntimeError):
        notices.deliver(session, MORNING, broken)
    assert session.exec(select(Notice)).one().sent_at is None
    phone = Phone()
    notices.deliver(session, MORNING + timedelta(minutes=15), phone)
    assert len(phone.messages) == 1


def test_a_thing_that_comes_back_is_told_again(session):
    conn = _expired_bank(session)
    phone = Phone()
    notices.deliver(session, MORNING, phone)
    conn.status = BankConnectionStatus.ACTIVE
    session.add(conn); session.commit()
    notices.deliver(session, MORNING + timedelta(days=1), phone)
    conn.status = BankConnectionStatus.EXPIRED
    session.add(conn); session.commit()
    notices.deliver(session, MORNING + timedelta(days=60), phone)
    assert len(phone.messages) == 2


def test_the_job_runs_every_fifteen_minutes_and_sends_through_telegram(session, monkeypatch):
    job = registry.JOBS["phone_notices"]
    assert job.default == Spec(kind="every_n_minutes", every_n=15) and job.quiet_when_ok
    monkeypatch.setattr(settings, "HUB_TELEGRAM_BOT_TOKEN", "synthetic-token")
    monkeypatch.setattr(settings, "HUB_REMINDER_TELEGRAM_NAME", "Pedro")
    monkeypatch.setenv("HUB_TELEGRAM_ALLOWED_USERS", "111:Rute,222:Pedro")
    sent = []
    monkeypatch.setattr(reminder_job, "telegram_send", lambda token, chat_id, text: sent.append((chat_id, text)))
    monkeypatch.setattr(notices, "quiet", lambda now: False)
    _expired_bank(session)
    out = asyncio.run(runners.run_phone_notices(session, "schedule"))
    assert out.outcome == "ok" and out.summary == "Sent on Telegram to Pedro: 1 thing that needs you."
    assert len(sent) == 1 and sent[0][0] == 222


def test_without_a_bot_and_with_something_to_tell_the_job_fails_visibly(session, monkeypatch):
    monkeypatch.setattr(settings, "HUB_TELEGRAM_BOT_TOKEN", "")
    out = asyncio.run(runners.run_phone_notices(session, "schedule"))
    assert out.outcome == "ok" and out.summary == "Nothing to tell."
    _expired_bank(session)
    out = asyncio.run(runners.run_phone_notices(session, "schedule"))
    assert out.outcome == "failed" and out.summary.startswith("The phone notices weren't sent.")
