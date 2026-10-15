"""Bank sync, statement reminder and mailbox check as scheduler jobs (decision D15): what is registered, what
each runner reports, and that deploying does not cause an extra bank sync. Synthetic data only; the bank
client, the mailbox and the Telegram send are the only fakes (the network edges). Bank errors come from the
real BankApiError / sync_link so the summaries are tested against the text the app actually stores."""

import asyncio
import imaplib
import socket
from datetime import date, datetime, timedelta, timezone
from functools import partial

import pytest
from sqlmodel import select
from telegram.error import Forbidden, InvalidToken, NetworkError

import app.routers.schedules  # noqa: F401
from app.channels.email_poller import FAILED_FOLDER, IGNORED_FOLDER, PROCESSED_FOLDER
from app.config import settings
from app.jobs import bank_sync as bank_job
from app.jobs import registry, runners, scheduler
from app.jobs import statement_reminder as reminder_job
from app.models.account import Account
from app.models.bank import BankAccountLink, BankApiCall, BankConnection, BankConnectionStatus
from app.models.document import Document, DocumentSource
from app.models.position import BalanceSnapshot, ReminderLog
from app.models.schedule import JobRun, ProblemEntry
from app.models.transaction import Transaction
from app.problems import Problem
from app.services.bankapi.client import BankApiError
from app.services.bankapi.sync import sync_link
from app.services.schedule import LISBON, Spec, validate
from app.services.statement_reminder import REMINDER_TEXT
from tests.test_email_poller import ALLOWED, FakeMailbox, _raw


def three_parts(text: str) -> None:
    """A message that says what didn't happen, why, and what to do: several sentences, the last one an action."""
    sentences = [x for x in text.replace("? ", ". ").split(". ") if x.strip()]
    assert len(sentences) >= 3, text


# ============================================================ what is registered ===

def test_the_defaults_are_todays_behaviour():
    jobs = registry.JOBS
    assert jobs["bank_sync"].default == Spec(kind="times_per_day", time_of_day="07:30,13:00,18:30")
    assert jobs["statement_reminder"].default == Spec(kind="daily", time_of_day="09:00")
    assert jobs["mailbox_check"].default == Spec(kind="every_n_minutes", every_n=10)
    assert jobs["wiki_lint"].default == Spec(kind="weekly", weekday=6, time_of_day="04:30")
    assert registry.FIXED_JOBS == []                                                  # nothing is left on a fixed timer


def test_every_default_passes_its_own_validation_and_limit():
    for job in registry.JOBS.values():
        validate(job.default, max_runs_per_day=job.max_runs_per_day, limit_reason=job.max_runs_reason, job_label=job.label)
        assert job.label and len(job.description) > 40 and asyncio.iscoroutinefunction(job.run)


def test_only_the_bank_has_a_daily_limit_and_it_is_four():
    assert {k: j.max_runs_per_day for k, j in registry.JOBS.items()} == {
        "mailbox_check": None, "statement_reminder": None, "check_nudge": None, "bank_sync": 4, "wiki_lint": None}
    assert "4 requests per account per day" in registry.JOBS["bank_sync"].max_runs_reason


def test_quick_frequent_jobs_run_before_the_slow_ones():
    keys = list(registry.JOBS)
    assert keys.index("mailbox_check") < keys.index("bank_sync") and keys.index("statement_reminder") < keys.index("bank_sync")


# ================================================================== bank sync ===

ROW = {"entry_reference": "r1", "status": "BOOK", "credit_debit_indicator": "DBIT",
       "transaction_amount": {"amount": "10.00", "currency": "EUR"}, "booking_date": "2026-10-14", "creditor": {"name": "Padaria"}}


class BankClient:
    """The bank's network edge. `by_uid` maps an account uid to rows or an exception to raise."""

    def __init__(self, by_uid):
        self.by_uid, self.calls = by_uid, []

    async def list_transactions(self, uid, date_from, date_to):
        self.calls.append(uid)
        what = self.by_uid[uid]
        if isinstance(what, Exception):
            raise what
        return what


async def fake_classify(session, transaction, client=None):
    transaction.merchant_id = None
    return None


@pytest.fixture()
def bank(session, monkeypatch):
    """A configured bank with a client the test controls; classification is the repo's usual fake."""
    monkeypatch.setattr(settings, "ENABLE_BANKING_APP_ID", "synthetic-app")
    monkeypatch.setattr(settings, "ENABLE_BANKING_KEY_PATH", "/nonexistent/synthetic.pem")
    state = {}

    def install(by_uid):
        state["client"] = BankClient(by_uid)
        monkeypatch.setattr(runners, "make_bank_client", lambda: state["client"])
        return state["client"]

    async def with_fake_classify(sess, client, link, **kw):
        return await sync_link(sess, client, link, classify=fake_classify, **kw)
    monkeypatch.setattr(bank_job, "sync_link", with_fake_classify)

    def link(uid, name, state_="st1"):
        conn = session.exec(select(BankConnection).where(BankConnection.state == state_)).first()
        if conn is None:
            conn = BankConnection(bank_name="Santander Totta", country="PT", state=state_, status=BankConnectionStatus.ACTIVE)
            session.add(conn); session.commit(); session.refresh(conn)
        acct = Account(name=name, institution="Santander"); session.add(acct); session.commit(); session.refresh(acct)
        lk = BankAccountLink(connection_id=conn.id, bank_account_uid=uid, account_id=acct.id, display_name=name)
        session.add(lk); session.commit(); session.refresh(lk)
        return lk
    return install, link


@pytest.mark.asyncio
async def test_bank_sync_that_is_not_set_up_does_nothing_and_says_so(session, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_BANKING_APP_ID", "")
    out = await runners.run_bank_sync(session, "schedule")
    assert out.outcome == "ok" and out.summary.startswith("Nothing to do.") and "no bank key" in out.summary


@pytest.mark.asyncio
async def test_bank_sync_with_an_unreadable_key_fails_in_three_parts(session, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_BANKING_APP_ID", "synthetic-app")
    monkeypatch.setattr(settings, "ENABLE_BANKING_KEY_PATH", "/nonexistent/synthetic.pem")
    out = await runners.run_bank_sync(session, "schedule")      # the real make_bank_client cannot read that file
    assert out.outcome == "failed" and out.summary.startswith("Bank sync didn't run.")
    assert "Tell Pedro" in out.summary and "key file" in out.summary
    three_parts(out.summary)


@pytest.mark.asyncio
async def test_bank_sync_with_no_active_account_is_a_quiet_success(session, bank):
    install, _ = bank
    install({})
    out = await runners.run_bank_sync(session, "schedule")
    assert out.outcome == "ok" and "No bank account is linked" in out.summary


@pytest.mark.asyncio
async def test_bank_sync_success_counts_what_it_saved(session, bank):
    install, link = bank
    lk = link("u1", "Conta Ordenado")
    client = install({"u1": [ROW, {**ROW, "entry_reference": "r2"}]})
    out = await runners.run_bank_sync(session, "schedule")
    assert out.outcome == "ok" and out.summary == "Done: 2 new transactions from 1 account."
    assert client.calls == ["u1"] and len(session.exec(select(Transaction)).all()) == 2
    assert session.get(BankAccountLink, lk.id).last_error is None


@pytest.mark.asyncio
async def test_bank_sync_when_the_bank_access_has_ended_fails_and_says_how_to_renew(session, bank):
    install, link = bank
    lk = link("u1", "Conta Ordenado")
    install({"u1": BankApiError(401, "SESSION_EXPIRED", "consent ended")})
    out = await runners.run_bank_sync(session, "schedule")
    assert out.outcome == "failed" and out.summary.startswith("Nothing was fetched from the bank.")
    assert "Santander Totta access has expired" in out.summary and "nothing will sync until you renew it" in out.summary and "Press Renew" in out.summary
    assert session.get(BankAccountLink, lk.id).last_error.startswith("Santander Totta access has expired.")        # what the production path stored
    three_parts(out.summary)


@pytest.mark.asyncio
@pytest.mark.parametrize("error,fragment", [
    (BankApiError(429, "RATE_LIMITED", "slow down"), "reached its limit of 4 requests per day"),
    (BankApiError(500, "SERVER_ERROR", "oops"), "answered, but not in a way the Hub could use"),
    (RuntimeError("boom"), "Something unexpected went wrong"),
])
async def test_bank_sync_failures_have_a_reason_and_a_next_step(session, bank, error, fragment):
    install, link = bank
    link("u1", "Conta Ordenado")
    install({"u1": error})
    out = await runners.run_bank_sync(session, "schedule")
    assert out.outcome == "failed" and fragment in out.summary
    assert "Conta Ordenado" in out.summary
    three_parts(out.summary)


@pytest.mark.asyncio
async def test_bank_sync_where_one_account_fails_is_partial_and_keeps_what_worked(session, bank):
    install, link = bank
    link("u1", "Conta Ordenado")
    link("u2", "Cartao")
    install({"u1": [ROW], "u2": BankApiError(429, "RATE_LIMITED", "slow down")})
    out = await runners.run_bank_sync(session, "schedule")
    assert out.outcome == "partial" and out.summary.startswith("Some bank accounts weren't fetched.")
    assert "Cartao" in out.summary and "Conta Ordenado" not in out.summary.split("The other accounts")[0]
    assert "1 new transaction saved" in out.summary
    assert len(session.exec(select(Transaction)).all()) == 1


@pytest.mark.asyncio
async def test_bank_sync_skips_an_account_that_used_its_four_requests(session, bank):
    install, link = bank
    lk = link("u1", "Conta Ordenado")
    for _ in range(4):
        session.add(BankApiCall(link_id=lk.id, called_at=datetime.utcnow() - timedelta(hours=1), kind="transactions"))
    session.commit()
    client = install({"u1": [ROW]})
    out = await runners.run_bank_sync(session, "schedule")
    assert out.outcome == "ok" and client.calls == [] and "limit of 4 requests per account per day" in out.summary


@pytest.mark.asyncio
async def test_bank_sync_reports_revolut_linking_trouble_as_partial(session, bank, monkeypatch):
    install, link = bank
    link("u1", "Conta Ordenado")
    install({"u1": [ROW]})

    def broken(_session):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(runners, "link_revolut_topups", broken)
    out = await runners.run_bank_sync(session, "schedule")
    assert out.outcome == "partial" and out.summary.startswith("Revolut top-ups weren't linked.")
    three_parts(out.summary)
    assert len(session.exec(select(Transaction)).all()) == 1                          # the fetched transactions were kept


@pytest.mark.asyncio
async def test_a_failed_bank_run_lands_on_the_problems_page_through_the_scheduler(session, bank):
    install, link = bank
    link("u1", "Conta Ordenado")
    install({"u1": BankApiError(401, "SESSION_EXPIRED", "consent ended")})
    run = await scheduler.run_job(session, "bank_sync", "schedule")
    assert run.outcome == "failed" and "Press Renew" in run.summary
    entries = {e.area: e for e in session.exec(select(ProblemEntry))}
    # two entries, each in its own place: the bank connection's own problem and the job run that failed
    assert set(entries) == {"Bank sync", "Scheduled jobs"}
    job = entries["Scheduled jobs"]
    assert job.ref == "job:bank_sync" and job.what == "Bank sync didn’t finish."
    assert entries["Bank sync"].what == "Santander Totta access has expired." and entries["Bank sync"].link


# --- not repeating a sync right after the deploy ---

def test_the_last_bank_request_stands_in_for_the_old_timers_last_run(session, bank):
    _, link = bank
    lk = link("u1", "Conta Ordenado")
    assert runners.bank_legacy_last_start(session) is None
    early, late = datetime(2026, 10, 15, 6, 30, 2), datetime(2026, 10, 15, 12, 0, 3)
    session.add(BankApiCall(link_id=lk.id, called_at=early, kind="transactions"))
    session.add(BankApiCall(link_id=lk.id, called_at=late, kind="transactions"))
    session.commit()
    assert runners.bank_legacy_last_start(session) == late


@pytest.mark.asyncio
async def test_deploying_does_not_cause_an_extra_bank_sync(session, bank, monkeypatch):
    install, link = bank
    lk = link("u1", "Conta Ordenado")
    client = install({"u1": []})
    monkeypatch.setattr(scheduler, "JOBS", {"bank_sync": registry.JOBS["bank_sync"]})
    # the old timer's 13:00 Lisbon run (12:00 UTC) made its request a moment later
    session.add(BankApiCall(link_id=lk.id, called_at=datetime(2026, 10, 15, 12, 0, 4), kind="transactions")); session.commit()
    assert await scheduler.run_due(session, datetime(2026, 10, 15, 12, 20, tzinfo=timezone.utc)) == []   # first check after the deploy
    assert await scheduler.run_due(session, datetime(2026, 10, 15, 17, 25, tzinfo=timezone.utc)) == []   # 18:25 Lisbon, before 18:30
    assert client.calls == []
    (run,) = await scheduler.run_due(session, datetime(2026, 10, 15, 17, 31, tzinfo=timezone.utc))        # 18:31: the evening slot
    assert run.trigger == "schedule" and client.calls == ["u1"]


@pytest.mark.asyncio
async def test_a_bank_that_was_never_asked_syncs_at_the_first_check(session, bank, monkeypatch):
    install, link = bank
    link("u1", "Conta Ordenado")
    client = install({"u1": []})
    monkeypatch.setattr(scheduler, "JOBS", {"bank_sync": registry.JOBS["bank_sync"]})
    assert len(await scheduler.run_due(session, datetime(2026, 10, 15, 12, 20, tzinfo=timezone.utc))) == 1 and client.calls == ["u1"]


# ============================================================ statement reminder ===

def _due_reminder(session) -> date:
    """Positions are six months old, so the reminder is due today (Lisbon date, as the runner counts it)."""
    today = datetime.now(LISBON).date()
    doc = Document(filename="s.pdf", file_path="/tmp/s.pdf", content_hash="reminder-run-1", source=DocumentSource.MANUAL)
    session.add(doc); session.commit(); session.refresh(doc)
    session.add(BalanceSnapshot(as_of=today - timedelta(days=185), kind="deposit", label="D", amount=1.0, document_id=doc.id))
    session.commit()
    return today


@pytest.fixture()
def telegram(monkeypatch):
    """A configured Telegram whose network send is replaced; records what was sent and from which thread state."""
    sent = []
    monkeypatch.setattr(settings, "HUB_TELEGRAM_BOT_TOKEN", "synthetic-token")
    monkeypatch.setattr(settings, "HUB_REMINDER_TELEGRAM_NAME", "Pedro")
    monkeypatch.setenv("HUB_TELEGRAM_ALLOWED_USERS", "111:Rute,222:Pedro")
    behaviour = {"raise": None}

    def send(token, chat_id, text):
        try:
            asyncio.get_running_loop()
            raise AssertionError("telegram_send uses asyncio.run, so it must not be called on a running event loop")
        except RuntimeError:
            pass
        if behaviour["raise"] is not None:
            raise behaviour["raise"]
        sent.append((token, chat_id, text))
    monkeypatch.setattr(reminder_job, "telegram_send", send)
    return sent, behaviour


@pytest.mark.asyncio
async def test_reminder_not_due_sends_nothing(session, telegram):
    sent, _ = telegram
    out = await runners.run_statement_reminder(session, "schedule")
    assert out.outcome == "ok" and "Nothing to send today" in out.summary and sent == []


@pytest.mark.asyncio
async def test_reminder_due_is_sent_once_with_the_exact_text(session, telegram):
    sent, _ = telegram
    _due_reminder(session)
    out = await runners.run_statement_reminder(session, "schedule")
    assert out.outcome == "ok" and out.summary == "The statement reminder was sent on Telegram to Pedro."
    assert sent == [("synthetic-token", 222, REMINDER_TEXT)] and len(session.exec(select(ReminderLog)).all()) == 1
    again = await runners.run_statement_reminder(session, "schedule")                  # the same cycle: only once
    assert again.outcome == "ok" and len(sent) == 1


@pytest.mark.asyncio
async def test_reminder_without_a_bot_token_fails_visibly_and_is_not_marked_sent(session, telegram, monkeypatch):
    sent, _ = telegram
    monkeypatch.setattr(settings, "HUB_TELEGRAM_BOT_TOKEN", "")
    _due_reminder(session)
    out = await runners.run_statement_reminder(session, "schedule")
    assert out.outcome == "failed" and out.summary.startswith("The statement reminder wasn't sent.")
    assert "no bot token" in out.summary and "Tell Pedro" in out.summary
    three_parts(out.summary)
    assert sent == [] and session.exec(select(ReminderLog)).all() == []


@pytest.mark.asyncio
async def test_reminder_with_an_unreadable_user_list_or_no_such_person_fails_in_three_parts(session, telegram, monkeypatch):
    _due_reminder(session)
    monkeypatch.setenv("HUB_TELEGRAM_ALLOWED_USERS", "not-a-number:Pedro")
    out = await runners.run_statement_reminder(session, "schedule")
    assert out.outcome == "failed" and "can't be read" in out.summary and "HUB_TELEGRAM_ALLOWED_USERS" in out.summary
    three_parts(out.summary)
    monkeypatch.setenv("HUB_TELEGRAM_ALLOWED_USERS", "111:Rute")
    out = await runners.run_statement_reminder(session, "schedule")
    assert out.outcome == "failed" and "No allowed Telegram user is named 'Pedro'" in out.summary
    three_parts(out.summary)


@pytest.mark.asyncio
@pytest.mark.parametrize("error,fragment", [
    (Forbidden("bot was blocked by the user"), "press Start"),
    (NetworkError("httpx.ConnectError: All connection attempts failed"), "couldn't be reached"),
    (InvalidToken("The token was rejected by the server."), "didn't accept the Hub's login"),
    (RuntimeError("synthetic"), "The Hub recorded this reason: RuntimeError: synthetic"),
])
async def test_reminder_send_failures_say_why_and_what_to_do(session, telegram, error, fragment):
    sent, behaviour = telegram
    _due_reminder(session)
    behaviour["raise"] = error
    out = await runners.run_statement_reminder(session, "schedule")
    assert out.outcome == "failed" and out.summary.startswith("The statement reminder wasn't sent.") and fragment in out.summary
    three_parts(out.summary)
    assert session.exec(select(ReminderLog)).all() == []                              # not marked: tomorrow's run retries
    behaviour["raise"] = None
    assert (await runners.run_statement_reminder(session, "schedule")).outcome == "ok" and len(sent) == 1


# ================================================================ mailbox check ===

@pytest.fixture()
def mail(monkeypatch):
    """A configured mailbox (the fake from the email poller tests) and a fake for the step that files attachments."""
    monkeypatch.setattr(settings, "HUB_IMAP_HOST", "imap.synthetic.test")
    monkeypatch.setattr(settings, "HUB_IMAP_USER", "hub@synthetic.test")
    monkeypatch.setattr(settings, "HUB_IMAP_PASSWORD", "synthetic")
    monkeypatch.setenv("HUB_IMAP_ALLOWED_SENDERS", ",".join(ALLOWED))
    received, behaviour = [], {"fail_on": None}

    async def receive(session, incoming, **kwargs):
        from app.services.inbox_service import InboxReceipt
        if behaviour["fail_on"] is not None and len(received) + 1 in behaviour["fail_on"]:
            received.append(None)
            raise RuntimeError("synthetic storage failure")
        received.append(incoming)
        return InboxReceipt(document=None, inbox_item=None, duplicate=False)
    monkeypatch.setattr(runners, "RECEIVE", receive)

    def install(messages):
        box = FakeMailbox(messages)
        box.closed = False
        original = box.close
        box.close = lambda: (setattr(box, "closed", True), original())
        monkeypatch.setattr(runners, "make_mailbox", lambda: box)
        return box
    return install, received, behaviour


@pytest.mark.asyncio
async def test_mailbox_that_is_not_set_up_does_nothing_and_says_so(session, monkeypatch):
    monkeypatch.setattr(settings, "HUB_IMAP_HOST", "")
    out = await runners.run_mailbox_check(session, "schedule")
    assert out.outcome == "ok" and out.summary.startswith("Nothing to do.") and "no mailbox login" in out.summary


@pytest.mark.asyncio
async def test_empty_mailbox(session, mail):
    install, _, _ = mail
    box = install({})
    out = await runners.run_mailbox_check(session, "schedule")
    assert (out.outcome, out.summary) == ("ok", "Nothing new in the mailbox.") and box.closed


@pytest.mark.asyncio
async def test_mail_with_attachments_is_filed_and_counted(session, mail):
    install, received, _ = mail
    box = install({b"1": _raw()})
    out = await runners.run_mailbox_check(session, "schedule")
    assert out.outcome == "ok" and out.summary == "Done: 2 documents filed in the Inbox from 1 email."
    assert len(received) == 2 and box.moves == [(b"1", PROCESSED_FOLDER)] and box.closed


@pytest.mark.asyncio
async def test_set_aside_mail_is_reported_without_alarm(session, mail):
    install, received, _ = mail
    install({b"1": _raw(sender="spam@evil.test")})
    out = await runners.run_mailbox_check(session, "schedule")
    assert out.outcome == "ok" and "1 email was set aside" in out.summary and "Hub-Ignored" in out.summary and received == []


@pytest.mark.asyncio
async def test_a_mail_that_could_not_be_filed_is_a_failure_with_a_next_step(session, mail):
    install, _, behaviour = mail
    behaviour["fail_on"] = {1}
    box = install({b"1": _raw()})
    out = await runners.run_mailbox_check(session, "schedule")
    assert out.outcome == "failed" and out.summary.startswith("1 email wasn't filed.")
    assert "Hub-Failed" in out.summary and "Forward them again" in out.summary
    three_parts(out.summary)
    assert box.moves == [(b"1", FAILED_FOLDER)]
    problem = session.exec(select(ProblemEntry)).one()                                 # the poller itself recorded the mail
    assert problem.area == "Email" and problem.what.startswith("An email wasn't filed")


@pytest.mark.asyncio
async def test_some_mail_filed_and_some_not_is_partial(session, mail):
    install, _, behaviour = mail
    behaviour["fail_on"] = {3}                                                         # the second mail's first attachment
    install({b"1": _raw(), b"2": _raw()})
    out = await runners.run_mailbox_check(session, "schedule")
    assert out.outcome == "partial" and out.summary.startswith("1 email wasn't filed.") and "The rest worked: 2 documents" in out.summary


@pytest.mark.asyncio
@pytest.mark.parametrize("error,fragment", [
    (imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)"), "didn't accept the Hub's login"),
    (socket.gaierror(-2, "Name or service not known"), "couldn't be reached"),
    (TimeoutError("timed out"), "couldn't be reached"),
])
async def test_a_mailbox_that_cannot_be_opened_fails_in_three_parts(session, mail, monkeypatch, error, fragment):
    def broken():
        raise error
    monkeypatch.setattr(runners, "make_mailbox", broken)
    out = await runners.run_mailbox_check(session, "schedule")
    assert out.outcome == "failed" and out.summary.startswith("The mailbox wasn't checked.") and fragment in out.summary
    three_parts(out.summary)


@pytest.mark.asyncio
async def test_a_failure_while_reading_the_mailbox_still_closes_it(session, mail, monkeypatch):
    install, _, _ = mail
    box = install({b"1": _raw()})

    def broken(*a, **k):
        raise TimeoutError("timed out")
    monkeypatch.setattr(box, "list_uids", broken)
    out = await runners.run_mailbox_check(session, "schedule")
    assert out.outcome == "failed" and box.closed


@pytest.mark.asyncio
async def test_the_scheduler_runs_the_mailbox_check_every_ten_minutes_and_keeps_its_history_short(session, mail, monkeypatch):
    install, _, _ = mail
    install({})
    monkeypatch.setattr(scheduler, "JOBS", {"mailbox_check": registry.JOBS["mailbox_check"]})
    now = datetime.now(timezone.utc)
    assert len(await scheduler.run_due(session, now)) == 1
    assert await scheduler.run_due(session, now + timedelta(minutes=5)) == []
    assert len(await scheduler.run_due(session, now + timedelta(minutes=10))) == 1
    assert [r.job for r in session.exec(select(JobRun)).all()] == ["mailbox_check", "mailbox_check"]
