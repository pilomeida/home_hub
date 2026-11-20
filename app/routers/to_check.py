"""The "To check" list (decision D11/D17): bank-statement lines the Hub was unsure about, and statements that do
not add up. The list is a section on the Inbox page and a page of its own (`/to-check`, the link the weekly
Telegram nudge sends). Every press answers with the card's new state (an HTMX fragment); a refusal comes back
in the card as a three-part message (app/problems.py), never silently."""


from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from app.db import get_session
from app.models.transaction import Transaction
from app.problems import render_html
from app.services import to_check
from app.services.to_check import ToCheckError, money
from app.templating import templates
from app.ui.to_check import line_card, section_context

router = APIRouter(prefix="/to-check", tags=["to-check"])

def _who(request: Request) -> str:
    return getattr(request.state, "user_email", None) or "someone in the family"


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
