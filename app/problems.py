"""Messages for what the Hub could not do (decision D14).

Every message has three parts, in this order:
  what   the fact, short:               "Not uploaded."
  why    the reason, in plain words:    "Bank statements have no privacy level yet, so nothing may read them."
  next   what the person can do:        "Set one on the Privacy page, then upload again."
A message that states the fact without the reason, or the reason without a way forward, is a defect.

Two ways in:
- written messages: `Problem(...)` built where the code knows exactly why (a hold, a refused file);
- technical reasons: `classify_reason(raw)` turns a stored exception/gateway text into a Cause (fact, why,
  next), and `compose(what, cause)` builds the message around the caller's own "what".
`record_problem` keeps a message in the Problems log so one that happened in the background, or in a
Telegram reply, can still be found. The raw text is never shown first: it is kept as "technical details"."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from markupsafe import Markup, escape
from sqlmodel import Session, select

from app.models.schedule import ProblemEntry


@dataclass(frozen=True)
class Cause:
    fact: str          # the short existing sentence, e.g. "The AI service couldn't be reached."
    why: str
    next_step: str


@dataclass(frozen=True)
class Problem:
    what: str
    why: str
    next_step: str
    link: Optional[str] = None

    @property
    def text(self) -> str:
        return f"{self.what} {self.why} {self.next_step}"


# --- classifying technical reasons -------------------------------------------------------------

_OUTAGE = re.compile(r"gateway returned\s+5\d\d|gateway returned 408|gateway unreachable", re.I)
_CLIENT_ERROR = re.compile(r"gateway returned\s+4\d\d", re.I)
_CREDIT = re.compile(r"out of credit|insufficient[_ ]quota|credit balance|payment required|returned 402", re.I)
_LEGACY_API = re.compile(
    r"error code:\s*\d+|authentication_error|permission_error|rate_limit_error|overloaded_error|"
    r"\bapi\b|\btimeout\b|\bunavailable\b|connection error|\banthropic\b", re.I)
_PDF = re.compile(r"\bpdf\b", re.I)
_UNDERSTOOD = re.compile(r"is not a valid|could not parse", re.I)
_TECHNICAL = re.compile(r"\b\w*(?:Error|Exception)\b|Traceback", re.I)
_NO_MODEL = re.compile(r"no_eligible_model|no supplier for model|has no model paired|does not satisfy", re.I)
_BLOCKED_SENS = re.compile(r"sensitivity[_ ]blocked|house_high has no route|no route is available", re.I)
_REDACT_FILE = re.compile(r"redaction requested but the call carries", re.I)
_REDACT_TOOLS = re.compile(r"redaction with tool use", re.I)
_REDACT_FAILED = re.compile(r"redaction (failed|incomplete)", re.I)
_TOO_LARGE = re.compile(r"413|too large|exceeds the \d+-token context|prompt_too_long", re.I)

_RETRY = "Nothing is lost: try again in a few minutes. If it keeps happening, tell Pedro."


def classify_reason(raw: Optional[str]) -> Optional[Cause]:
    """A Cause for a technical-looking stored reason, or None when the text is already plain English."""
    if not raw:
        return None
    if _REDACT_FILE.search(raw):
        return Cause("Hiding personal details first isn't possible for this file.",
                     "That setting only works on text, and this file is a PDF or photo that has to be sent as an image.",
                     "Switch “Hide personal details” off for this type of document on the Privacy page, or send a text version.")
    if _REDACT_TOOLS.search(raw):
        return Cause("Hiding personal details first isn't possible for this question.",
                     "That setting doesn't work yet when the AI also has to look things up.",
                     "Switch “Hide personal details” off for the types involved on the Privacy page.")
    if _REDACT_FAILED.search(raw):
        return Cause("The Hub couldn't hide the personal details, so nothing was sent.",
                     "The step that hides them failed or left something visible, and the Hub never sends a document half-hidden.",
                     "Try again. If it repeats, tell Pedro: the technical details below will help.")
    if _BLOCKED_SENS.search(raw):
        return Cause("The AI wasn't allowed to read this document.",
                     "Its privacy level is “High”, and no AI service is approved for that level yet.",
                     "Choose a different level for this type on the Privacy page, or file it without reading.")
    if _CREDIT.search(raw):
        return Cause("The AI service couldn't be used.",
                     "The account that pays for it has run out of credit.",
                     "Top up the AI provider account, then try again.")
    if _PDF.search(raw):
        return Cause("The file couldn't be read.",
                     "It may be damaged, password-protected or not really a PDF.",
                     "Open it yourself to check; if it is protected, remove the password and upload it again.")
    if _NO_MODEL.search(raw):
        return Cause("This document couldn't be read automatically.",
                     "No AI model is currently allowed to do this job under its privacy and strength settings.",
                     "Check “The AI jobs” on the Privacy page, or enter the details by hand.")
    if _TOO_LARGE.search(raw):
        return Cause("This document couldn't be read automatically.",
                     "It is too big for the AI to take in one go.",
                     "Split it into smaller files (for example one statement month at a time) and upload them separately.")
    if _OUTAGE.search(raw) or _LEGACY_API.search(raw):
        return Cause("The AI service couldn't be reached.",
                     "It didn't answer, usually because it is restarting or busy.", _RETRY)
    if _CLIENT_ERROR.search(raw):
        return Cause("This document couldn't be read automatically.",
                     "The AI service refused the request, usually because of a setting rather than an outage.",
                     "Enter the details by hand, and tell Pedro if it keeps happening.")
    if _UNDERSTOOD.search(raw):
        return Cause("Some details in this document couldn't be understood.",
                     "The AI's answer wasn't in the form the Hub expects, or a value in it wasn't valid.",
                     "Check the details below and correct them by hand; trying again sometimes works.")
    if _TECHNICAL.search(raw):
        return Cause("Something went wrong processing this document.",
                     "An unexpected error happened inside the Hub.",
                     "Try again. If it repeats, tell Pedro and give him the technical details below.")
    return None


def compose(what: str, cause: Cause, *, actionable: Optional[str] = None) -> Problem:
    """The three-part message around the caller's own fact. `actionable` replaces the generic next step
    when the page knows a better one (e.g. “Enter it manually below.”)."""
    return Problem(what=what, why=cause.why, next_step=actionable or cause.next_step)


def explain(what: str, raw: Optional[str], *, actionable: Optional[str] = None) -> Problem:
    cause = classify_reason(raw) or Cause(what, "The Hub recorded this reason: " + (raw or "no reason was recorded") + ".",
                                          actionable or "Try again; if it repeats, tell Pedro.")
    return compose(what, cause, actionable=actionable)


def render_html(problem: Problem, technical: Optional[str] = None) -> Markup:
    body = f"<strong>{escape(problem.what)}</strong> {escape(problem.why)} {escape(problem.next_step)}"
    if problem.link:
        body += f' <a href="{escape(problem.link)}">Open</a>'
    if technical:
        body += f"<details><summary>Technical details</summary><code>{escape(technical)}</code></details>"
    return Markup(body)


def explain_lint_errors(errors: Optional[str], *, whole_run: bool) -> str:
    """Plain-language reason for a wiki check that failed or only partly worked."""
    cause = classify_reason(errors)
    lead = "Not done." if whole_run else "Some of it couldn't be done."
    if cause is None:
        return f"{lead} The Hub recorded: {errors or 'no reason'}. Run the check again; if it repeats, tell Pedro."
    area = "" if whole_run else " The checks that don't need AI did finish."
    return f"{lead} {cause.why}{area} {cause.next_step}"


# --- the Problems log -------------------------------------------------------------------------------

def record_problem(session: Session, *, area: str, problem: Problem, ref: Optional[str] = None,
                   technical: Optional[str] = None) -> ProblemEntry:
    """Log a problem. The same unresolved problem for the same thing is counted, not duplicated."""
    existing = session.exec(select(ProblemEntry).where(
        ProblemEntry.ref == ref, ProblemEntry.what == problem.what, ProblemEntry.area == area,
        ProblemEntry.resolved_at == None)).first() if ref else None   # noqa: E711
    if existing is not None:
        existing.times += 1
        existing.at = datetime.utcnow()
        existing.why, existing.next_step, existing.technical = problem.why, problem.next_step, technical
        session.add(existing)
        session.commit()
        return existing
    entry = ProblemEntry(area=area, what=problem.what, why=problem.why, next_step=problem.next_step,
                         link=problem.link, technical=(technical or "")[:2000] or None, ref=ref)
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def resolve_for(session: Session, ref: str, by: Optional[str] = None) -> int:
    rows = session.exec(select(ProblemEntry).where(ProblemEntry.ref == ref, ProblemEntry.resolved_at == None)).all()  # noqa: E711
    for r in rows:
        r.resolved_at, r.resolved_by = datetime.utcnow(), by or "resolved by itself"
        session.add(r)
    session.commit()
    return len(rows)


def open_problems(session: Session, limit: int = 100) -> list[ProblemEntry]:
    return list(session.exec(select(ProblemEntry).where(ProblemEntry.resolved_at == None)  # noqa: E711
                             .order_by(ProblemEntry.at.desc()).limit(limit)))


def open_count() -> int:
    """For the menu. Opens its own session; never raises (a menu must not break a page)."""
    try:
        from sqlmodel import func
        from app.db import engine
        with Session(engine) as s:
            return s.exec(select(func.count()).select_from(ProblemEntry).where(ProblemEntry.resolved_at == None)).one()  # noqa: E711
    except Exception:
        return 0


# --- web errors (HTTPException) -> three-part messages -------------------------------------------
# Exact detail strings first, then patterns, then a fallback by status. Nothing here changes what the
# routes raise; the handler in app/main.py turns it into a message a person can act on.

_HTTP_EXACT: dict[str, tuple[str, str, str]] = {
    "Pick a sub-category to apply": ("Nothing was changed.", "No sub-category was chosen, so there is nothing to apply.", "Pick one from the list, then press the button again."),
    "Tick at least one description": ("Nothing was changed.", "No descriptions were ticked, so there is nothing to apply the change to.", "Tick the ones you want, then try again."),
    "Tick at least one merchant": ("Nothing was changed.", "No merchants were ticked, so there is nothing to apply the change to.", "Tick the ones you want, then try again."),
    "Unknown category": ("Not saved.", "The category you chose doesn't exist (the list may have changed since this page was opened).", "Reload the page and choose again."),
    "Ambiguous or unknown category; pick a tree category": ("Not saved.", "That name matches several categories, or none.", "Pick a category from the list instead of typing it."),
    "Choose at least one file": ("Nothing was uploaded.", "No file was chosen.", "Choose a file and try again."),
    "This printout does not need a loan assignment": ("Not assigned.", "This kind of printout isn't tied to one loan.", "Nothing to do — it is filed correctly."),
    "Only missing-link findings can add a link": ("Link not added.", "This finding isn't a missing-link finding, so there is no link to add.", "Use Dismiss or Fixed on it instead."),
    "Unknown domain": ("Not done.", "The area you chose doesn't exist (the page may be out of date).", "Reload the page and choose again."),
    "Bank connections aren't set up on this server yet": ("Bank connection unavailable.", "The keys that let the Hub talk to the bank haven't been installed on the server yet.", "Tell Pedro; the setup steps are in the bank section of the server notes."),
    "Not in the Inbox": ("Couldn't open that Inbox item.", "It isn't in the Inbox any more — someone may already have filed or discarded it.", "Reload the Inbox to see what is waiting."),
    "That document isn't in the Inbox.": ("Not filed.", "That document isn't in the Inbox any more — someone may already have filed or discarded it.", "Reload the Inbox to see what is waiting."),
    "That document has already been handled.": ("Not filed again.", "Someone already filed or discarded this document.", "Reload the Inbox; nothing more is needed."),
    "The previous question is still being answered": ("Question not sent.", "The answer to your previous question isn't finished yet, and Ask handles one question at a time.", "Wait for the answer to appear, then ask again."),
    "Only answered questions can be saved": ("Not saved to the wiki.", "This question has no finished answer yet, so there is nothing to save.", "Wait for the answer (or ask again if it failed), then save it."),
    "Unknown utility tab": ("Couldn't open that page.", "There is no such utility tab.", "Go back to Utilities and pick one from the list."),
    # --- added by pages-loans-utilities agent ---
    "Unknown alert kind": ("Not acknowledged.", "That kind of red flag doesn't exist, so the Hub can't tell which ones to acknowledge.", "Reload the Loans page and try again."),
    "The amount must be a number": ("Not saved.", "The amount has letters or symbols in it, so the Hub can't read it as money.", "Type the amount as a number, for example 250.00, then save again."),
    "The amount must be greater than zero": ("Not saved.", "An amount of zero or less can't be recorded: the kind you pick (advance, repayment, adjustment) already sets the direction.", "Type the size of the amount as a positive number, then save again."),
    "The date must look like 2026-01-31": ("Not saved.", "The date isn't in the year-month-day form the Hub reads.", "Pick the date from the calendar, or type it as 2026-01-31, then save again."),
    "Give the person's name": ("Not saved.", "The loan needs the name of the person it is with, so it can be found later.", "Type their name, then create the loan again."),
    "Choose who owes whom": ("Not saved.", "The Hub needs to know whether they owe you or you owe them, and no choice was made.", "Choose one of the two options, then create the loan again."),
    "Choose advance, repayment or an adjustment": ("Not saved.", "The kind of entry wasn't one of the four choices in the list.", "Pick advance, repayment, or an adjustment up or down, then save again."),
    "An entry made from a bank transaction cannot be deleted": ("Not deleted.", "It was copied from a bank transaction, and the Hub keeps those so the loan matches the bank.", "To correct the amount, add an adjustment entry here instead."),
    "This printout is not waiting for a loan": ("Not assigned.", "Its figures were already read, or it could not be read, so there is no loan to choose.", "Nothing to do here. To retry an unreadable printout, upload it again."),
}
_HTTP_PATTERNS: list[tuple[re.Pattern, callable]] = [
    (re.compile(r"^Upload at most (\d+) files at a time$"),
     lambda m: ("Nothing was uploaded.", f"More than {m[1]} files were chosen at once.", f"Upload {m[1]} or fewer at a time, then repeat for the rest.")),
    (re.compile(r"^The note is at most (\d+) characters$"),
     lambda m: ("Note not saved.", f"It is longer than {m[1]} characters.", "Shorten it and save again.")),
    (re.compile(r"^Question must be 1.(\d+) characters$"),
     lambda m: ("Question not sent.", f"It is empty or longer than {m[1]} characters.", "Write a question of up to that length and send it again.")),
    (re.compile(r"^(.+) not found$", re.I),
     lambda m: ("Couldn't open that.", f"The Hub has no {m[1].lower()} with that number; it may have been deleted or the link is out of date.", "Go back and choose it from the list again.")),
    (re.compile(r"^No such (.+)$", re.I),
     lambda m: ("Couldn't open that.", f"There is no {m[1]} with that number; it may have been removed.", "Go back and choose it from the list again.")),
    # --- added by pages-loans-utilities agent ---
    (re.compile(r"^The name is at most (\d+) characters$"),
     lambda m: ("Not saved.", f"It is longer than {m[1]} characters.", "Shorten it and save again.")),
]


def for_http(status: int, detail: object) -> Problem:
    if isinstance(detail, dict) and {"what", "why", "next_step"} <= set(detail):    # a route that wrote its own
        return Problem(detail["what"], detail["why"], detail["next_step"], detail.get("link"))
    text = detail if isinstance(detail, str) else "".join(str(detail))
    if text in _HTTP_EXACT:
        what, why, nxt = _HTTP_EXACT[text]
        return Problem(what, why, nxt)
    for pattern, build in _HTTP_PATTERNS:
        m = pattern.match(text)
        if m:
            what, why, nxt = build(m)
            return Problem(what, why, nxt)
    cause = classify_reason(text)
    if cause is not None:
        return compose("Not done.", cause)
    if status == 404:
        return Problem("Couldn't open that.", "The page or item doesn't exist.", "Go back and choose it from the menu.")
    if status == 401:
        return Problem("Not signed in.", "Your sign-in has expired or wasn't recognised.", "Reload the page and sign in again.")
    if status == 413:
        return Problem("Not uploaded.", "The file is larger than the 100 MB the web server accepts.", "Reduce its size or split it, then upload again.")
    if status == 422:
        return Problem("Not saved.", "Some of what was entered isn't in the form the Hub expects.", "Check the fields on the page and try again.")
    if status >= 500:
        return Problem("Something broke.", "The Hub hit an unexpected error while doing this.",
                       "Try again. If it repeats, tell Pedro; the Problems page keeps the technical details.", "/problems")
    if status == 409:
        return Problem("Not done.", text or "It conflicts with the current state of the data.", "Reload the page and try again.")
    return Problem("Not done.", text or "The request wasn't accepted.", "Correct what is on the page and try again.")


# --- added by pages-other agent ---
# Refusals the services raise as plain English (inline notices on the Transactions, House, Insurance and
# Documents pages), with the wording for each. A row is (what, why, next step). A text not listed here still
# gets all three parts through explain(); its own text is then kept as the reason.

_NOTICE_TEXT: dict[str, tuple[str, str, str]] = {
    # lending or borrowing from a payment (Needs Review, "Link Debt")
    "Transaction not found": ("Not linked.", "The transaction isn't in the Hub any more; it may have been deleted, or the page is out of date.", "Reload Needs Review and try again."),
    "Debt not found": ("Not linked.", "The loan you picked no longer exists; it may have been deleted, or the list is out of date.", "Reload Needs Review and choose the loan again."),
    "The role must be advance or repayment": ("Not linked.", "The role has to be either an advance (more lent or borrowed) or a repayment.", "Choose one of the two and press Link Debt again."),
    "This transaction is already linked to a debt": ("Not linked.", "This transaction already belongs to a loan, and one transaction can only be on one loan.", "Nothing to do here: reload Needs Review to see what is still waiting."),
    "This transaction is already linked to another debt": ("Not linked.", "This transaction already belongs to a different loan, and one transaction can only be on one loan.", "Leave it where it is; moving it to another loan isn't possible from this page."),
    "Unknown direction": ("Not linked.", "The direction isn't one the Hub knows: a loan is either money we lent or money we borrowed.", "Choose one of the two and press Link Debt again."),
    "Choose the direction: a transfer does not say whether you lent or borrowed": ("Not linked.", "A transfer doesn't say whether the money was lent or borrowed, so the Hub needs you to choose.", "In the new-loan box choose “we lent” or “we borrowed”, then press Link Debt again."),
    "The name is at most 80 characters": ("Not linked.", "The person's name is longer than 80 characters.", "Shorten the name and press Link Debt again."),
    "Give the person's name": ("Not linked.", "A new loan needs the name of the person it is with.", "Type their name in the new-loan box and press Link Debt again."),
    "Choose a direction": ("Not linked.", "A new loan needs a direction: money we lent, or money we borrowed.", "Choose one of the two and press Link Debt again."),
    "Choose a role (advance or repayment): a transfer has no reliable direction": ("Not linked.", "A transfer doesn't say whether money was lent or borrowed, so the role can't be worked out by itself.", "Choose advance or repayment in the role box and press Link Debt again."),
    "The role does not fit this transaction and direction: money going out starts a loan we gave, money coming in starts a loan we received": ("Not linked.", "The role doesn't fit this payment: money going out starts a loan we gave, and money coming in starts a loan we received.", "Choose the role that matches, or leave it on automatic, and press Link Debt again."),
    # files and records (House, Insurance, Documents)
    "No file was chosen to upload": ("Nothing was uploaded.", "No file was chosen, so there was nothing to send.", "Choose a file, then press Upload again."),
    "Choose a file to upload": ("Nothing was uploaded.", "The Hub doesn't recognise this kind of file.", "Choose a PDF or a photo of it, then press Upload again."),
    "No file was chosen to attach": ("Nothing was attached.", "No file was chosen, so there was nothing to attach.", "Choose a file, then press Attach again."),
    "This file is already filed elsewhere": ("Not filed.", "The Hub already has this exact file, filed under something else, and keeps one copy of each file.", "Find it under its current place, or choose a different file."),
    "This file is attached to a hand-entered record; edit the record instead.": ("Not saved.", "This file belongs to a record typed in by hand, and changes are made on that record.", "Open the record and edit it there."),
    "This file is attached to a hand-entered record; re-file the record instead.": ("Not moved.", "This file belongs to a record typed in by hand, so the record is what has to move.", "Open the record and re-file it from there."),
    "This record already has a file attached.": ("Not attached.", "A record holds one file, and this one already has a file.", "Open the record to see the file it has."),
    "This record was already re-filed.": ("Not saved.", "This record has already been moved to another category or area, so this page is out of date.", "Reload the page and open the record again."),
    "A hand-entered record can only become a document if it has a file attached.": ("Not moved.", "A record typed in by hand has no file, and a document needs one.", "Attach a file to the record first, then move it."),
}
_KIND_NAMES = {"pdf": "PDF", "image": "photo", "video": "video"}
_NOTICE_PATTERNS: list[tuple[re.Pattern, callable]] = [
    (re.compile(r"^(.+) accepts (.+) files only$"),
     lambda m: ("Not saved.", f"{m[1]} takes only {' or '.join(_KIND_NAMES.get(k.strip(), k.strip()) for k in m[2].split(','))} files, and this file is another kind.",
                "Choose a file of one of those kinds, then try again.")),
    (re.compile(r"^(.+) does not accept this file type$"),
     lambda m: ("Not attached.", f"{m[1]} does not accept this kind of file.", "Choose a file of a type this category takes (usually a PDF or a photo), then try again.")),
    (re.compile(r"^Unknown (.+) category$"),
     lambda m: ("Not saved.", f"The {m[1]} category you chose doesn't exist (the list may have changed since the page was opened).",
                "Reload the page and choose again.")),
    (re.compile(r"^(.+) is filed from a document, not entered by hand$"),
     lambda m: ("Not saved.", f"{m[1]} is filled in from its document, so it can't be typed in by hand.", "Upload the document instead.")),
    (re.compile(r"^(\d+) transaction\(s\) from this document are linked to a commitment, debt or transfer\."),
     lambda m: ("Not moved.", f"{m[1]} transaction(s) from this document are linked to a commitment, debt or transfer, and moving the file would discard those links.", "Remove those links in Transactions first, then move the file again.")),
]


def _notice_row(text: str) -> Optional[tuple[str, str, str]]:
    row = _NOTICE_TEXT.get(text)
    if row is None:
        for pattern, build in _NOTICE_PATTERNS:
            m = pattern.match(text)
            if m:
                return build(m)
    return row


def notice_problem(text: str, what: str = "Not saved.") -> Problem:
    """The three-part message for a refusal a service raised as plain English. Not classified as a technical
    failure on purpose: a file-type refusal mentions "PDF" and must not be read as a damaged file."""
    row = _notice_row(text)
    if row is None:
        return Problem(what, text.rstrip(".") + ".", "Check what is on the page and try again; if it repeats, tell Pedro.")
    return Problem(*row)


def notice(text: str, what: str = "Not saved.") -> str:
    """The same message as one line of text, for an inline notice on a page."""
    return notice_problem(text, what).text


def file_notices(errors: dict) -> dict:
    """The errors dict for a template with file refusals written out in three parts. A category hint is
    rewritten only when it is a known refusal; instructions such as "Choose a category" stay as they are."""
    out = {}
    for key, value in errors.items():
        if isinstance(value, str) and (key == "file" or (key == "category" and _notice_row(value) is not None)):
            out[key] = notice(value)
        else:
            out[key] = value
    return out
# --- end pages-other agent ---


# --- added by jobs agent ---
# Background jobs that talk to a service that is not the AI (the bank, the mailbox, Telegram). The causes in
# `classify_reason` are written about the AI service and documents ("The AI service couldn't be reached"), which
# would mislead here, so these have their own wording; anything else falls back to the same "recorded this reason" form.

_SVC_LOGIN = re.compile(r"LOGIN failed|AUTHENTICATIONFAILED|authentication failed|invalid credentials|"
                        r"InvalidToken|Unauthorized|\b401\b|\b403\b", re.I)
_SVC_NETWORK = re.compile(r"timed out|timeout|connection (refused|reset|aborted)|NetworkError|ConnectError|gaierror|"
                          r"name or service not known|temporary failure in name resolution|SSLError|EOF occurred|"
                          r"unreachable|ConnectionError|BrokenPipe", re.I)


def explain_service_failure(service: str, what: str, raw: Optional[str], *, actionable: Optional[str] = None) -> Problem:
    """Three-part message for a background job whose call to `service` (e.g. "The Hub mailbox") failed with `raw`."""
    if raw and _SVC_LOGIN.search(raw):
        return Problem(what, f"{service} didn't accept the Hub's login; the saved password or key may have been changed or may have expired.",
                       actionable or "Tell Pedro: the login saved on the server needs updating.")
    if raw and _SVC_NETWORK.search(raw):
        return Problem(what, f"{service} couldn't be reached; the internet connection or the service itself may be down for a moment.",
                       actionable or "Nothing is lost: the next scheduled run tries again. If it keeps failing for hours, tell Pedro.")
    return Problem(what, f"The Hub recorded this reason: {raw or 'no reason was recorded'}.",
                   actionable or "Try again at the next run; if it repeats, tell Pedro.")
# --- end of block added by jobs agent ---
