"""The "To check" list (decision D11/D17): bank-statement lines the Hub was unsure about, and statements that do
not add up. The list is a section on the Inbox page and a page of its own (`/to-check`, the link the weekly
Telegram nudge sends). Every press answers with the card's new state (an HTMX fragment); a refusal comes back
in the card as a three-part message (app/problems.py), never silently."""

import re
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from app.db import get_session
from app.domains.fields import file_url, media_kind_for
from app.models.document import Document
from app.models.transaction import Transaction
from app.problems import render_html
from app.services import to_check
from app.services.to_check import ToCheckError, money
from app.templating import templates

router = APIRouter(prefix="/to-check", tags=["to-check"])

_FIELD_WORDS = {"date": ("date", "day"), "description": ("description", "name", "merchant", "text"),
                "amount": ("amount", "value", "sum", "total", "balance", "€", "eur")}


def doubtful_fields(reason: Optional[str]) -> set[str]:
    """Which values to highlight. The reason is plain words and does not name a field in a fixed way, so the
    fields it mentions are highlighted; when it names none, all three are (any of them could be the doubtful one)."""
    tokens = re.findall(r"[a-z€]+", (reason or "").lower())
    found = {field for field, words in _FIELD_WORDS.items() if any(t.startswith(w) for t in tokens for w in words)}
    return found or {"date", "description", "amount"}


def _who(request: Request) -> str:
    return getattr(request.state, "user_email", None) or "someone in the family"


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


def _fragment(name: str, status: int = 200, **ctx) -> HTMLResponse:
    return HTMLResponse(templates.get_template(name).render(**ctx), status_code=status)


def _refused(exc: ToCheckError, card_id: str) -> HTMLResponse:
    return _fragment("to_check/_refused.html", exc.status, card_id=card_id, problem_html=render_html(exc.problem))


@router.get("")
async def page(request: Request, session: Session = Depends(get_session)):
    ctx = section_context(session)
    if request.headers.get("hx-request"):
        return _fragment("to_check/_section.html", **ctx)
    return templates.TemplateResponse(request, "to_check/page.html", ctx)


def _waiting(session: Session, transaction_id: int) -> Transaction:
    transaction = session.get(Transaction, transaction_id)
    if transaction is None or transaction.review_status != to_check.NEEDS_CHECK:
        raise to_check._gone("Not shown.", transaction)
    return transaction


@router.get("/lines/{transaction_id}")
async def line(transaction_id: int, session: Session = Depends(get_session)):
    try:
        transaction = _waiting(session, transaction_id)
    except ToCheckError as exc:
        return _refused(exc, f"tc-line-{transaction_id}")
    return HTMLResponse(line_card(session, transaction))


@router.get("/lines/{transaction_id}/edit")
async def edit(transaction_id: int, session: Session = Depends(get_session)):
    try:
        transaction = _waiting(session, transaction_id)
    except ToCheckError as exc:
        return _refused(exc, f"tc-line-{transaction_id}")
    return HTMLResponse(line_card(session, transaction, editing=True))


def _done(transaction: Transaction, corrected: bool = False) -> HTMLResponse:
    return _fragment("to_check/_done.html", t=transaction, amount_text=money(transaction.amount), corrected=corrected)


@router.post("/lines/{transaction_id}/confirm")
async def confirm(request: Request, transaction_id: int, session: Session = Depends(get_session)):
    try:
        return _done(to_check.confirm(session, transaction_id, _who(request)))
    except ToCheckError as exc:
        return _refused(exc, f"tc-line-{transaction_id}")


@router.post("/lines/{transaction_id}/reject")
async def reject(request: Request, transaction_id: int, session: Session = Depends(get_session)):
    try:
        return _done(to_check.reject(session, transaction_id, _who(request)))
    except ToCheckError as exc:
        return _refused(exc, f"tc-line-{transaction_id}")


@router.post("/lines/{transaction_id}/correct")
async def correct(request: Request, transaction_id: int, session: Session = Depends(get_session)):
    form = {k: v for k, v in (await request.form()).items() if isinstance(v, str)}
    try:
        return _done(to_check.correct(session, transaction_id, _who(request), date=form.get("date"),
                                      description=form.get("description"), amount=form.get("amount")), corrected=True)
    except ToCheckError as exc:
        transaction = session.get(Transaction, transaction_id)
        if exc.status != 422 or transaction is None or transaction.review_status != to_check.NEEDS_CHECK:
            return _refused(exc, f"tc-line-{transaction_id}")
        # Invalid input: show the form again with what was typed and the message, nothing was changed.
        typed = {k: form.get(k, "") for k in ("date", "description", "amount")}
        return HTMLResponse(line_card(session, transaction, editing=True, values=typed,
                                      problem_html=render_html(exc.problem)), status_code=exc.status)


@router.post("/statements/{document_id}/accept")
async def accept(request: Request, document_id: int, session: Session = Depends(get_session)):
    try:
        check = to_check.accept_difference(session, document_id, _who(request))
    except ToCheckError as exc:
        return _refused(exc, f"tc-stmt-{document_id}")
    return _fragment("to_check/_statement_done.html", check=check)
