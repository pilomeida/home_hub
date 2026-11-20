"""The "To check" cards and section (decision D11/D17): one card per doubtful bank-statement line, grouped by
statement. Shown on the Inbox page and on /to-check."""

import re
from types import SimpleNamespace
from typing import Optional

from sqlmodel import Session

from app.categories.fields import file_url, media_kind_for
from app.models.document import Document
from app.models.transaction import Transaction
from app.services import to_check
from app.services.to_check import money
from app.templating import templates

_FIELD_WORDS = {"date": ("date", "day"), "description": ("description", "name", "merchant", "text"),
                "amount": ("amount", "value", "sum", "total", "balance", "€", "eur")}


def doubtful_fields(reason: Optional[str]) -> set[str]:
    """Which values to highlight. The reason is plain words and does not name a field in a fixed way, so the
    fields it mentions are highlighted; when it names none, all three are (any of them could be the doubtful one)."""
    tokens = re.findall(r"[a-z€]+", (reason or "").lower())
    found = {field for field, words in _FIELD_WORDS.items() if any(t.startswith(w) for t in tokens for w in words)}
    return found or {"date", "description", "amount"}


def _kind(document: Document) -> str:
    kind = media_kind_for(document.filename).value
    return kind if kind in ("pdf", "image") else "other"


def _line_context(transaction: Transaction, document: Document, **extra) -> dict:
    when = to_check.line_date(transaction)
    return {
        "t": transaction, "file_href": file_url(document), "source_kind": _kind(document),
        "doubtful": doubtful_fields(transaction.review_reason),
        "date_text": when.strftime("%Y-%m-%d") if when else "—", "amount_text": money(transaction.amount),
        "editing": False, "problem_html": None,
        "values": {"date": when.isoformat() if when else "", "description": transaction.description,
                   "amount": f"{transaction.amount:.2f}"},
        **extra,
    }


def line_card(session: Session, transaction: Transaction, **extra) -> str:
    document = session.get(Document, transaction.document_id)
    return templates.get_template("to_check/_line.html").render(**_line_context(transaction, document, **extra))


def section_context(session: Session) -> dict:
    groups = []
    for group in to_check.items(session):
        groups.append(SimpleNamespace(
            document=group.document, file_href=file_url(group.document), mismatch=group.mismatch,
            mismatch_sentence=to_check.mismatch_sentence(group.mismatch) if group.mismatch else "",
            cards=[line_card(session, t) for t in group.lines]))
    return {"groups": groups, "total": to_check.count(session)}


def section_html(session: Session) -> str:
    """The section as HTML, for the Inbox page."""
    return templates.get_template("to_check/_section.html").render(**section_context(session))
