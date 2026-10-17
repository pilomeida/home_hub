"""The runners of the jobs that used to have their own systemd timers: bank sync, statement reminder and
mailbox check (decision D15). Each one calls the SAME functions the old `main()` called, with the same
arguments and settings, and turns the result into a JobOutcome whose summary says what happened and, when
something did not work, what didn't happen, why, and what to do (see app/problems.py).

Seams for tests: `make_bank_client`, `make_mailbox` and `RECEIVE` (the network edges) can be replaced; nothing
else is."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from sqlmodel import Session, func, select

from app.channels import email_poller
from app.config import settings
from app.jobs import bank_sync as bank_job
from app.jobs import statement_reminder as reminder_job
from app.jobs.outcome import JobOutcome
from app.models.bank import BankAccountLink, BankApiCall, BankConnection
from app.problems import Problem, explain, explain_service_failure
from app.services.bankapi.client import EnableBankingClient
from app.services.bankapi.topups import link_revolut_topups
from app.services.schedule import LISBON
from app.services.statement_reminder import reminder_to_send

logger = logging.getLogger("home_hub.job_runners")


def _failed(problem: Problem) -> JobOutcome:
    return JobOutcome("failed", problem.text)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# ============================================================ bank sync ===

BANK_LIMIT_REASON = "the bank allows only 4 requests per account per day"


def make_bank_client():
    return EnableBankingClient(settings.ENABLE_BANKING_APP_ID, Path(settings.ENABLE_BANKING_KEY_PATH).read_bytes())


def bank_legacy_last_start(session: Session) -> Optional[datetime]:
    """When the old fixed timer (or a "Sync now") last asked the bank. Every request is written to the
    call ledger before it is made, so this is the last time the bank was contacted; switching to the Hub's
    scheduler then does not cause an extra sync in the same slot."""
    return session.exec(select(func.max(BankApiCall.called_at))).one()


def _account_label(session: Session, link_id: int) -> str:
    link = session.get(BankAccountLink, link_id)
    if link is None:
        return f"account {link_id}"
    connection = session.get(BankConnection, link.connection_id)
    name = link.display_name or (f"account ending {link.iban[-4:]}" if link.iban else f"account {link.id}")
    return f"{connection.bank_name} {name}" if connection is not None else name


def _bank_cause(error: str) -> tuple[str, str]:
    """(why, what to do) for the error text sync_link stored on a failed account."""
    e = error.lower()
    if "access has ended" in e:
        return ("the Hub's permission to read this account has run out (banks grant it only for a limited time)",
                "Renew the bank connection on the Bank connections page (Financials, Bank); nothing else is needed.")
    if "wait" in e:
        return ("the bank asked the Hub to slow down",
                "Nothing to do: the next scheduled run tries again.")
    if "didn't answer" in e:
        return ("the bank's service answered with an error",
                "Nothing is lost: the next scheduled run tries again. If it still fails tomorrow, tell Pedro.")
    if "saving" in e:
        return ("the Hub hit a problem while saving what the bank sent, so some transactions may be missing",
                "Tell Pedro. The next run fetches the same period again and never saves a transaction twice.")
    if "unexpected" in e:
        return ("the Hub hit an unexpected error while fetching it",
                "Nothing is lost: the next scheduled run tries again. If it repeats, tell Pedro; the log has the technical details.")
    problem = explain("Not fetched.", error)
    return problem.why.rstrip("."), problem.next_step


def _bank_failure_text(session: Session, failed: dict, *, everything: bool, saved: int) -> str:
    """`failed` maps account id -> the structured Problem sync_link produced (or, for an older stored
    error, its plain text). Each distinct problem is told once, in its own three parts."""
    what = "Nothing was fetched from the bank." if everything else "Some bank accounts weren't fetched."
    told: list[str] = []
    legacy: dict[tuple[str, str], list[str]] = {}
    for link_id, err in failed.items():
        if isinstance(err, Problem):
            if err.text not in told:
                told.append(err.text)
        else:
            legacy.setdefault(_bank_cause(err), []).append(_account_label(session, link_id))
    for (cause, step), names in legacy.items():
        told.append(f"{', '.join(names)}: {cause}. {step}")
    done = "" if everything else f" The other accounts worked ({_plural(saved, 'new transaction', 'new transactions')} saved)."
    return f"{what} {' '.join(told)}{done}"


async def run_bank_sync(session: Session, trigger: str) -> JobOutcome:
    """The old `app.jobs.bank_sync._main(dry_run=False)`: every active, mapped account, then the Revolut top-up linking."""
    if not settings.bank_configured:
        return JobOutcome("ok", "Nothing to do. The bank connection isn't set up on this server (no bank key saved), "
                                "so no accounts are fetched.")
    try:
        client = make_bank_client()
    except Exception as exc:
        return _failed(explain_service_failure(
            "The bank key file", "Bank sync didn't run.", f"{type(exc).__name__}: {exc}",
            actionable="Tell Pedro: the key file the Hub uses to sign in to the bank is missing or can't be read on the server."))
    results = await bank_job.run_all(session, client, dry_run=False, scheduled=(trigger == "schedule"))
    topups_failed = False
    try:
        link_revolut_topups(session)
    except Exception:
        logger.exception("Linking Revolut top-ups failed")
        session.rollback()
        topups_failed = True

    failed = {i: (r.problem or r.error) for i, r in results.items() if r.error or r.problem and not r.skipped_quota}
    skipped = [i for i, r in results.items() if r.skipped_quota and not r.error]
    created = sum(r.created for r in results.values())
    unclassified = sum(r.unclassified for r in results.values())
    if results and len(failed) == len(results):
        return JobOutcome("failed", _bank_failure_text(session, failed, everything=True, saved=0))
    if failed:
        return JobOutcome("partial", _bank_failure_text(session, failed, everything=False, saved=created))
    if topups_failed:
        return _partial(Problem(
            "Revolut top-ups weren't linked.",
            "The transactions were fetched, but matching Revolut top-ups to the bank movements that paid for them hit an unexpected error.",
            "Nothing is lost: they are linked at the next run. If it repeats, tell Pedro."))
    if not results:
        return JobOutcome("ok", "Nothing to do. No bank account is linked, active and mapped to an account, so there was nothing to fetch.")
    if len(skipped) == len(results):
        return JobOutcome("ok", "Nothing fetched. Every account had already used the bank's limit of 4 requests per account per day "
                                "(manual syncs count too). The next scheduled run tries again.")
    text = f"Done: {_plural(created, 'new transaction', 'new transactions')} from {_plural(len(results) - len(skipped), 'account', 'accounts')}."
    if unclassified:
        text += f" {_plural(unclassified, 'transaction', 'transactions')} couldn't be sorted automatically and wait in Needs Review."
    if skipped:
        text += f" {_plural(len(skipped), 'account was', 'accounts were')} skipped: the bank's daily limit of 4 requests was already used."
    return JobOutcome("ok", text)


def _partial(problem: Problem) -> JobOutcome:
    return JobOutcome("partial", problem.text)


# ================================================== statement reminder ===

_TELEGRAM_CLOSED = re.compile(r"Forbidden|blocked by the user|chat not found|bot was blocked", re.I)


async def run_statement_reminder(session: Session, trigger: str) -> JobOutcome:
    """The old `app.jobs.statement_reminder.main()`: send the reminder when it is due (once per cycle), else do nothing."""
    today: date = datetime.now(LISBON).date()
    if not reminder_to_send(session, today):
        return JobOutcome("ok", "Nothing to send today. The next statement reminder isn't due yet, or it was already sent for this cycle.")
    not_sent = "The statement reminder wasn't sent."
    token = settings.HUB_TELEGRAM_BOT_TOKEN
    if not token:
        return _failed(Problem(not_sent, "It is due, but the Telegram bot isn't set up on this server (no bot token saved).",
                               "Tell Pedro to add the bot token to the server settings; the reminder is sent at the next daily run after that."))
    try:
        users = settings.telegram_allowed_users
    except ValueError as exc:
        return _failed(Problem(not_sent, f"The list of allowed Telegram users on the server can't be read ({exc}).",
                               "Tell Pedro to correct HUB_TELEGRAM_ALLOWED_USERS on the server; the reminder is sent at the next daily run after that."))
    name = settings.HUB_REMINDER_TELEGRAM_NAME
    if reminder_job.find_recipient(users, name) is None:
        return _failed(Problem(not_sent, f"No allowed Telegram user is named {name!r}, so there is nobody to send it to.",
                               "Tell Pedro to add that person to the allowed Telegram users (or change who receives the reminder) on the server."))

    errors: list[Exception] = []

    def send(tok: str, chat_id: int, text: str) -> None:
        try:
            reminder_job.telegram_send(tok, chat_id, text)
        except Exception as exc:
            errors.append(exc)
            raise

    # run() is the old job body; it sends with asyncio.run(), so it must not run on the scheduler's own event loop.
    sent = await asyncio.to_thread(reminder_job.run, session, today, send=send, token=token, users=users, name=name)
    if sent:
        return JobOutcome("ok", f"The statement reminder was sent on Telegram to {name}.")
    raw = f"{type(errors[0]).__name__}: {errors[0]}" if errors else None
    if raw and _TELEGRAM_CLOSED.search(raw):
        return _failed(Problem(not_sent, "Telegram wouldn't deliver it: the chat with the Hub bot is closed or the bot was blocked.",
                               "Open the Hub bot in Telegram and press Start (or unblock it); the reminder is sent at the next daily run."))
    return _failed(explain_service_failure("Telegram", not_sent, raw or "the send did not complete"))


# ====================================================== things to check ===

async def run_check_nudge(session: Session, trigger: str) -> JobOutcome:
    """The weekly nudge (decision D11): ONE Telegram message to the statement reminder's recipient saying how many
    things wait on the To check list, only when there are some. Sending and the recipient lookup are the reminder's own."""
    from app.services import to_check

    waiting = to_check.count(session)
    if not waiting:
        return JobOutcome("ok", "Nothing to check this week.")
    things = _plural(waiting, "thing is", "things are")
    not_sent = "The “things to check” message wasn't sent."
    retry = "it is sent at the next scheduled run after that, or press Run now on the Scheduled jobs page."
    token = settings.HUB_TELEGRAM_BOT_TOKEN
    if not token:
        return _failed(Problem(not_sent, f"{things.capitalize()} waiting to be checked, but the Telegram bot isn't set up on this server (no bot token saved).",
                               f"Tell Pedro to add the bot token to the server settings; {retry}"))
    try:
        users = settings.telegram_allowed_users
    except ValueError as exc:
        return _failed(Problem(not_sent, f"The list of allowed Telegram users on the server can't be read ({exc}).",
                               f"Tell Pedro to correct HUB_TELEGRAM_ALLOWED_USERS on the server; {retry}"))
    name = settings.HUB_REMINDER_TELEGRAM_NAME
    chat_id = reminder_job.find_recipient(users, name)
    if chat_id is None:
        return _failed(Problem(not_sent, f"No allowed Telegram user is named {name!r}, so there is nobody to send it to.",
                               "Tell Pedro to add that person to the allowed Telegram users (or change who receives the reminder) on the server."))
    text = f"{things.capitalize()} waiting to be checked in the Hub: {settings.PUBLIC_BASE_URL.rstrip('/')}/to-check"
    try:
        await asyncio.to_thread(reminder_job.telegram_send, token, chat_id, text)    # it calls asyncio.run: not on this loop
    except Exception as exc:
        raw = f"{type(exc).__name__}: {exc}"
        if _TELEGRAM_CLOSED.search(raw):
            return _failed(Problem(not_sent, "Telegram wouldn't deliver it: the chat with the Hub bot is closed or the bot was blocked.",
                                   f"Open the Hub bot in Telegram and press Start (or unblock it); {retry}"))
        return _failed(explain_service_failure("Telegram", not_sent, raw))
    return JobOutcome("ok", f"Sent on Telegram to {name}: {things} waiting to be checked.")


# ===================================================== mailbox check ===

RECEIVE = email_poller.receive_document        # what files an attachment in the Inbox (replaced only in tests)


def make_mailbox():
    return email_poller.ImapMailbox(settings.HUB_IMAP_HOST, settings.HUB_IMAP_PORT,
                                    settings.HUB_IMAP_USER, settings.HUB_IMAP_PASSWORD)


def _poll_blocking(bind) -> email_poller.PollReport:
    """The old `email_poller.main()` body. imaplib blocks, so this runs in its own thread with its own event loop."""
    mailbox = make_mailbox()
    try:
        return asyncio.run(email_poller.poll_once(mailbox, lambda: Session(bind), settings.imap_allowed_senders, receive=RECEIVE))
    finally:
        mailbox.close()


async def run_mailbox_check(session: Session, trigger: str) -> JobOutcome:
    if not settings.imap_configured:
        return JobOutcome("ok", "Nothing to do. The Hub mailbox isn't set up on this server (no mailbox login saved), so no email is picked up.")
    try:
        report = await asyncio.to_thread(_poll_blocking, session.get_bind())
    except Exception as exc:
        logger.exception("mailbox check failed")
        return _failed(explain_service_failure("The Hub mailbox", "The mailbox wasn't checked.", f"{type(exc).__name__}: {exc}"))
    if report.failed:
        what = f"{_plural(report.failed, 'email wasn', 'emails weren')}'t filed."
        problem = Problem(what, "The Hub couldn't read it, or hit an unexpected error while storing its attachment; each one is listed on the Problems page.",
                          "The mails are kept in the mailbox's Hub-Failed folder. Forward them again as a normal email with the PDF or photo attached; if it repeats, tell Pedro.")
        filed = f" The rest worked: {_plural(report.documents_created, 'document', 'documents')} filed in the Inbox." if report.documents_created else ""
        return JobOutcome("partial" if report.processed else "failed", problem.text + filed)
    if not (report.processed or report.ignored):
        return JobOutcome("ok", "Nothing new in the mailbox.")
    text = ""
    if report.processed:
        text = f"Done: {_plural(report.documents_created, 'document', 'documents')} filed in the Inbox from {_plural(report.processed, 'email', 'emails')}."
        notes = []
        if report.duplicates:
            notes.append(f"{_plural(report.duplicates, 'attachment was an exact copy', 'attachments were exact copies')} of documents already in the Hub")
        if report.decorative:
            notes.append(f"{_plural(report.decorative, 'image was a logo or banner', 'images were logos or banners')}")
        if notes:
            text += " Not added: " + " and ".join(notes) + " (listed under “Not added” on the Inbox page)."
    if report.ignored:
        text += f"{' ' if text else ''}{_plural(report.ignored, 'email was', 'emails were')} set aside (unknown sender, or nothing to file); they are in the mailbox's Hub-Ignored folder."
    return JobOutcome("ok", text)
