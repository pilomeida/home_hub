"""Messages for everything that can go wrong with the bank connection and the sync (decision D14).

Every message says what didn't happen, why, and what the person can do. They are built here, once, so the
sync, the job, the bank pages and the notices on Bills & Bank all say the same thing, and so the Problems
log (area "Bank sync") holds the same words the person saw."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.problems import Problem
from app.services.presentation import lisbon_datetime

AREA = "Bank sync"
BANK_PAGE = "/financials/bank/"
DAILY_LIMIT = 4          # requests per account per day the bank allows (mirrors sync.DAILY_CALL_LIMIT)

_RETRY_LATER = ("Nothing to do and nothing is lost: the next automatic sync (07:30, 13:00 and 18:30) will "
                "try again.")


def connection_ref(connection_id: int) -> str:
    return f"bank-connection:{connection_id}"


def expiring_ref(connection_id: int) -> str:
    return f"bank-connection-expiring:{connection_id}"


def link_ref(link_id: int) -> str:
    return f"bank-link:{link_id}"


def balance_ref(link_id: int) -> str:
    return f"bank-balance:{link_id}"


def mismatch_ref(link_id: int) -> str:
    return f"bank-mismatch:{link_id}"


def label(bank_name: str, display_name: Optional[str] = None) -> str:
    """How a person calls one account: 'Conta (Santander Totta)'."""
    return f"{display_name} ({bank_name})" if display_name else bank_name


def _days(n: int) -> str:
    return f"{n} day{'s' if n != 1 else ''}"


# --- the bank's permission --------------------------------------------------------------------------------

def consent_expired(bank_name: str) -> Problem:
    return Problem(
        f"{bank_name} access has expired.",
        "The bank only lets the Hub read your accounts for a limited time and that time is over, "
        "so nothing will sync until you renew it.",
        "Press Renew next to the bank on the Bank connections page and approve in the bank's app; "
        "your account mapping is kept.",
        BANK_PAGE)


def consent_expiring(bank_name: str, days: int) -> Problem:
    return Problem(
        f"{bank_name} access expires " + ("today." if days <= 0 else f"in {_days(days)}."),
        "The bank's permission for the Hub lasts a limited time. When it runs out, new transactions stop "
        "arriving until it is renewed.",
        "Press Renew next to the bank on the Bank connections page and approve in the bank's app; "
        "it takes about a minute.",
        BANK_PAGE)


# --- one account that did not update ----------------------------------------------------------------------

def daily_budget_used(account: str) -> Problem:
    """Our own count says the 4 requests the bank allows for this account today are already spent."""
    return Problem(
        f"{account} wasn't updated this time.",
        f"The bank allows only {DAILY_LIMIT} requests per account per day, and all {DAILY_LIMIT} for this "
        "account were already used in the last 24 hours (the automatic syncs and “Sync now” share them).",
        _RETRY_LATER, BANK_PAGE)


def balance_not_read(account: str, *, reached: bool = True) -> Problem:
    """The account's transactions came through, but its balance could not be read."""
    why = ("the bank didn't answer the request for the balance." if reached
           else "the Hub could not reach the bank to ask for the balance.")
    return Problem(
        f"The balance of {account} was not updated.",
        f"Its transactions were fetched, but {why}",
        "Nothing to do now: the next automatic sync asks again. Until then the Cash figure shows the last balance the bank gave.",
        BANK_PAGE)


def balance_mismatch(account: str, bank: float, ours: float) -> Problem:
    """The Hub's lines, rolled forward from the bank's last balance, do not reach the bank's new balance."""
    gap = ours - bank
    return Problem(
        f"The Hub's figure for {account} is off by €{abs(gap):,.2f}.",
        f"The bank says the account holds €{bank:,.2f}; the lines the Hub stored since the last check add up to "
        f"€{ours:,.2f}, which is €{abs(gap):,.2f} {'too high' if gap > 0 else 'too low'}. "
        "A line may be stored twice, or the bank may have booked a line the Hub has not received yet.",
        "Compare the account's latest lines on the Transactions page with the bank. If a line shows twice, tell me and "
        "I will exclude the copy. A missing line arrives by itself at the next sync, and this note clears when the figures agree.",
        BANK_PAGE)


def bank_rate_limited(account: str) -> Problem:
    """The bank itself answered 'too many requests'."""
    return Problem(
        f"{account} wasn't updated this time.",
        f"The bank said this account has reached its limit of {DAILY_LIMIT} requests per day.",
        _RETRY_LATER, BANK_PAGE)


def bank_not_answering(account: str, *, reached: bool = True) -> Problem:
    why = ("The bank answered, but not in a way the Hub could use." if reached
           else "The Hub couldn't reach the bank, which is usually a short outage on their side or on the server.")
    return Problem(
        f"{account} wasn't updated this time.", why,
        "Nothing is lost: the next automatic sync (07:30, 13:00 and 18:30) will try again. "
        "If it is still failing tomorrow, tell Pedro: the technical details are on the Problems page.",
        "/problems")


def save_failed(account: str) -> Problem:
    return Problem(
        f"{account} was only partly updated.",
        "The Hub got the transactions from the bank but hit an unexpected error while saving them, "
        "so some may be missing for now.",
        "Nothing is lost: the next sync fetches them again and skips the ones already saved. "
        "If it happens again, tell Pedro: the technical details are on the Problems page.",
        "/problems")


def unexpected_failure(account: str) -> Problem:
    return Problem(
        f"{account} wasn't updated this time.",
        "Something unexpected went wrong inside the Hub while fetching it; the other accounts were not affected.",
        "Nothing is lost: the next automatic sync will try again. If it keeps happening, tell Pedro: "
        "the technical details are on the Problems page.",
        "/problems")


def not_updated_for_days(account: str, last_synced_at: Optional[datetime]) -> Problem:
    since = f" since {lisbon_datetime(last_synced_at)}" if last_synced_at else ""
    return Problem(
        f"{account} hasn't updated{since}.",
        "The automatic syncs, which run three times a day, haven't completed for it in over two days, "
        "which usually means the bank isn't answering.",
        "Press “Sync now” on the Bank connections page. If it still doesn't update, tell Pedro.",
        BANK_PAGE)


def last_attempt_failed(account: str, bank_name: str) -> Problem:
    """Fallback for a stored failure whose Problems entry is gone (dismissed, or written before this log existed)."""
    return Problem(
        f"{account} hasn't updated.",
        "The last attempt to fetch its transactions failed.",
        f"The reason is shown under {bank_name} on the Bank connections page; press “Sync now” there to try again.",
        BANK_PAGE)


# --- the Hub side -----------------------------------------------------------------------------------------

def nothing_linked(bank_name: str) -> Problem:
    return Problem(
        "Nothing was synced.",
        f"None of {bank_name}'s accounts is linked to a Hub account yet, so there is nowhere to put their transactions.",
        "Open “Map accounts” for the bank and choose a Hub account for each one you want to track.",
        BANK_PAGE)


def account_not_linked(bank_name: str) -> Problem:
    return Problem(
        f"Some {bank_name} accounts aren't linked.",
        "Their transactions aren't being pulled in because no Hub account is chosen for them.",
        "Use “Map accounts” to choose one for each, or leave it if you chose not to track them.",
        BANK_PAGE)


def hub_busy() -> Problem:
    return Problem(
        "Not updated.",
        "The Hub is busy right now: its database was in use by something else, so the sync couldn't start.",
        "Try “Sync now” again in a minute; nothing was lost.",
        BANK_PAGE)


def waiting_for_approval(bank_name: str) -> Problem:
    return Problem(
        f"{bank_name} isn't connected yet.",
        "The Hub is waiting for you to approve the connection in the bank's app, and nothing syncs until you do.",
        "Approve it in the bank's app; if you closed it or it timed out, press “Try again” to start over.",
        BANK_PAGE)


# --- connecting a bank (the person is looking at these) ---------------------------------------------------

def connect_not_started(reason: str) -> Problem:
    return Problem(
        "The bank connection wasn't started.", reason,
        "Try again in a moment with Connect or Renew; if it keeps happening, tell Pedro.", BANK_PAGE)


def unknown_bank() -> Problem:
    return Problem(
        "The bank connection wasn't started.",
        "That bank isn't one the Hub can connect to (the page may be out of date).",
        "Reload the Bank connections page and choose one of the banks listed there.", BANK_PAGE)


def returned_without_finishing() -> Problem:
    return Problem(
        "Nothing was connected.",
        "The bank sent you back without finishing, usually because the approval in the bank's app was "
        "cancelled or timed out.",
        "Press Connect or Renew on the Bank connections page again, and approve in the bank's app.", BANK_PAGE)


def link_used_or_expired() -> Problem:
    return Problem(
        "Nothing was connected.",
        "This approval link has expired or was already used (each one works once).",
        "Start again with Connect or Renew on the Bank connections page.", BANK_PAGE)


def connection_not_finished() -> Problem:
    return Problem(
        "Nothing was saved.",
        "You approved at the bank, but the bank didn't hand the Hub the account details.",
        "Try connecting again from the Bank connections page; if it keeps happening, tell Pedro.", BANK_PAGE)


def unknown_account_choice(connection_id: int) -> Problem:
    return Problem(
        "Not saved.",
        "That Hub account doesn't exist (the list may have changed since this page was opened).",
        "Pick one of the listed Hub accounts on the mapping page and save again.",
        f"{BANK_PAGE}{connection_id}/map")


def bank_not_set_up() -> dict:
    return {"what": "Bank connection unavailable.",
            "why": "The keys that let the Hub talk to the bank haven't been installed on the server yet.",
            "next_step": "Tell Pedro; the setup steps are in the bank section of the server notes."}


def as_detail(problem: Problem) -> dict:
    """A route's own message, in the shape `HTTPException(detail=...)` carries to the central handler."""
    d = {"what": problem.what, "why": problem.why, "next_step": problem.next_step}
    if problem.link:
        d["link"] = problem.link
    return d


# --- the Hub side of the sync job ----------------------------------------------------------------------------

def sync_setup_missing() -> Problem:
    return Problem(
        "Bank sync is switched off.",
        "A bank is connected, but the keys the Hub needs to talk to the bank aren't installed on the server, "
        "so no bank has been synced.",
        "Tell Pedro to reinstall the bank keys on the server; syncing resumes at the next scheduled run.",
        "/problems")


def topups_not_linked() -> Problem:
    return Problem(
        "Revolut top-ups weren't matched to their Santander transfers.",
        "The matching step hit an unexpected error after the bank sync finished; the synced transactions are "
        "saved, but the two sides of a top-up may still count as spending and income.",
        "Nothing is lost: the next sync tries the matching again. If it keeps happening, tell Pedro: "
        "the technical details are on the Problems page.",
        "/problems")
