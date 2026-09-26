"""Shared presentation helpers -- the ONE place a stored technical string
(a Document.failure_reason, an InboxItem.classifier_note, ...) is turned
into something a person can read, and the only place a date/datetime or an
internal claim/fact key is formatted for display. Registered as Jinja
filters on the shared app.templating.templates environment; some are also
called directly from Python (e.g. overview_service's Needs Attention list)
so both paths share one implementation.

Nothing here changes what gets stored -- callers keep writing the raw
technical reason exactly as before. This module only decides how it is
shown."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

from markupsafe import Markup, escape

_GENERIC_REASON_MESSAGE = "Something went wrong processing this document."

# Matches a raw failure_reason that came from an Anthropic API call failing
# (auth, credit, rate limit, timeout, service unavailable, ...) -- these are
# the "Error code: NNN - {...}"-shaped messages the Anthropic SDK raises,
# plus the plainer "API timeout" / "credit balance too low" wording this
# codebase also produces.
_API_PATTERN = re.compile(
    r"error code:\s*\d+|authentication_error|permission_error|rate_limit_error|"
    r"overloaded_error|insufficient_quota|credit balance|\bapi\b|\btimeout\b|"
    r"\bunavailable\b|connection error|\banthropic\b",
    re.IGNORECASE,
)
_PDF_PATTERN = re.compile(r"\bpdf\b", re.IGNORECASE)
_VALUE_ERROR_PATTERN = re.compile(r"is not a valid|could not parse", re.IGNORECASE)
# A broader "this still looks like raw exception text" signal, for anything
# that doesn't match one of the specific buckets above but clearly isn't
# hand-written English either (a bare Python exception repr).
_GENERIC_TECHNICAL_PATTERN = re.compile(
    r"\b\w*(?:Error|Exception)\b|Traceback", re.IGNORECASE
)


def _friendly_sentence(reason: str) -> Optional[str]:
    """The mapped friendly sentence for a technical-looking reason, or
    None when the reason doesn't look like a raw technical error at all
    (in which case it's already plain English and shown unchanged)."""
    # File problems first: the API rejects a broken PDF with an "Error code:
    # 400" that would otherwise read as an unreachable service.
    if _PDF_PATTERN.search(reason):
        return "The file couldn't be read."
    if _API_PATTERN.search(reason):
        return "The AI service couldn't be reached."
    if _VALUE_ERROR_PATTERN.search(reason):
        return "Some details in this document couldn't be understood."
    if _GENERIC_TECHNICAL_PATTERN.search(reason):
        return _GENERIC_REASON_MESSAGE
    return None


def humanize_reason_text(reason: Optional[str]) -> str:
    """Plain-text friendly summary for one-line contexts that can't hold a
    <details> block (e.g. the Overview's needs-attention list). Falls back
    to the original text when it isn't a recognized technical error."""
    if not reason:
        return ""
    return _friendly_sentence(reason) or reason


def friendly_reason(reason: Optional[str], actionable: Optional[str] = None) -> Markup:
    """Jinja filter: a short plain-English sentence for a stored technical
    failure reason, with the original technical text kept available but
    secondary in a collapsed <details> block. `actionable` is the page's
    own call-to-action sentence (e.g. "Enter it manually below."),
    appended after the friendly sentence -- only when the reason was
    actually recognized as technical; a reason that's already plain
    English (e.g. "duplicate — matched existing transaction") is passed
    through unchanged, since it's not a raw error needing translation."""
    if not reason:
        return Markup("")
    sentence = _friendly_sentence(reason)
    if sentence is None:
        return Markup(escape(reason))
    if actionable:
        sentence = f"{sentence} {actionable}"
    # `sentence` is always one of our own trusted strings (the mapped
    # message plus the caller's own actionable text) -- never escaped, so
    # it reads naturally ("couldn't", not "couldn&#39;t"). Only `reason`
    # (the raw technical text) is untrusted and needs escaping.
    return Markup(
        f"{sentence}"
        f"<details><summary>Technical details</summary><code>{escape(reason)}</code></details>"
    )


def humanize_key(key: Optional[str]) -> str:
    """An internal snake_case claim/fact key as a readable label, e.g.
    "statement_period" -> "Statement period". Used only when the claim has
    no explicit label (legacy or Financials claims)."""
    if not key:
        return ""
    return key.replace("_", " ").capitalize()


def display_date(value: Optional["date | datetime"]) -> str:
    """A date or datetime as House's existing nice date format, e.g.
    "18 Aug 2026"."""
    if not value:
        return "—"
    return value.strftime("%d %b %Y")


def display_datetime(value: Optional[datetime]) -> str:
    """A datetime as "18 Aug 2026, 09:05" -- the one shared format for
    every timestamp shown to a person (wiki page/log, Inbox, Ask, Wiki
    check)."""
    if not value:
        return "—"
    return value.strftime("%d %b %Y, %H:%M")


def claim_label(label: Optional[str], key: Optional[str]) -> str:
    """A claim's display label: its own label when it has a real one,
    otherwise the humanized key (legacy claims were backfilled with
    label == key, which is no label at all)."""
    if label and label != key:
        return label
    return humanize_key(key)
