"""Loans & Savings page: loans, funds, deposits, cards and informal debts."""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import logging
import math
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from starlette.datastructures import UploadFile as StarletteUploadFile
from sqlmodel import Session, select

from app.db import get_session
from app.domains.fields import InvalidClassification
from app.models.category_node import CategoryNode
from app.models.debt import Debt, DebtDirection
from app.models.document import Document, DocumentSource
from app.models.domain import Domain
from app.models.person import Person
from app.models.position import DebtEntry, LoanAlert, LoanMovement, LoanSnapshot, PositionExtraction
from app.models.transaction import Transaction, TransactionType
from app.services.ingestion import Classification, IncomingFile, ingest
from app.services import debt_ledger
from app.services.loan_insurance import LOAN_INSURANCE_SLUGS
from app.services.loan_alerts import (
    KINDS as ALERT_KINDS, MAX_NOTE_CHARS as MAX_ACK_NOTE, SPREAD_TOLERANCE, acknowledge_alert, acknowledge_group, group_alerts,
)
from app.services.loan_math import (
    LoanSummary, informal_debt_rows, informal_summary, newest_balances, position_totals, savings_lines, summarize_loan, summarize_all,
)
from app.services.position_store import (
    assign_history_to_loan, create_loan_from_assignment, extraction_summary, loan_number_conflict,
    process_loan_history_document, process_positions_document,
)
from app.problems import Problem, classify_reason
from app.services.pdf_bytes import TRUNCATED, inspect_pdf
from app.services.statement_reminder import UPLOAD_URL
from app.templating import templates

router = APIRouter(prefix="/financials/loans", tags=["loans"])

STALE_AFTER_DAYS = 150
MAX_UPLOAD_FILES = 12
MAX_FILE_BYTES = 25 * 1024 * 1024  # per file; a bigger one is rejected on its own
LOAN_TYPES = ("mortgage", "personal", "other")
log = logging.getLogger(__name__)

STATEMENTS, HISTORIES = "statements", "histories"
_SLOT = {
    STATEMENTS: {"label": "monthly statement", "category": "statement_positions",
                 "compatible": {"statement", "statement_positions"}, "process": process_positions_document},
    HISTORIES: {"label": "loan history printout", "category": "loan_history",
                "compatible": {"loan_history"}, "process": process_loan_history_document},
}


@dataclass
class FileResult:
    name: str
    slot: str
    outcome: str  # read | already_read | failed | needs_loan | rejected | other
    message: str = ""
    extraction_id: Optional[int] = None
    problem: Optional[Problem] = None  # what / why / next step, for every outcome that did not work
    technical: Optional[str] = None  # the raw stored text, shown only under "Technical details"
    warnings: list = field(default_factory=list)  # Problems for a read that left something out


@dataclass
class LoanRow:
    summary: LoanSummary
    from_statements: bool
    rate_derived: bool
    remaining_pct: Optional[float]
    payoff_text: Optional[str]
    alerts: list = field(default_factory=list)  # unacknowledged LoanAlert rows
    groups: list = field(default_factory=list)  # the same, grouped per kind (AlertGroup)
    acked_alerts: list = field(default_factory=list)
    insurance_paid: float = 0.0  # linked insurance debits (SEG...), beside the instalment
    last_premium: Optional[float] = None
    last_premium_date: Optional[date] = None


def payoff_text(months_left: Optional[int], payoff_date: Optional[date]) -> Optional[str]:
    if months_left is None or payoff_date is None:
        return None
    years, months = divmod(months_left, 12)
    parts = []
    if years:
        parts.append(f"{years} yr{'s' if years != 1 else ''}")
    if months or not years:
        parts.append(f"{months} mo")
    return f"≈ {' '.join(parts)} ({payoff_date.strftime('%b %Y')})"


def _insurance_debits(session: Session, debt_id: int) -> tuple[float, Optional[float], Optional[date]]:
    """(total, latest premium, its date) of the debits linked to the loan AND filed
    under the Loan insurance nodes."""
    node_ids = session.exec(select(CategoryNode.id).where(CategoryNode.slug.in_(LOAN_INSURANCE_SLUGS))).all()
    if not node_ids:
        return 0.0, None, None
    rows = session.exec(
        select(Transaction).where(
            Transaction.debt_id == debt_id, Transaction.transaction_type == TransactionType.DEBIT,
            Transaction.counts(), Transaction.category_id.in_(node_ids),
        ).order_by(Transaction.paid_date, Transaction.id)
    ).all()
    if not rows:
        return 0.0, None, None
    return round(sum(t.amount for t in rows), 2), rows[-1].amount, rows[-1].paid_date


def _loan_row(session: Session, summary: LoanSummary) -> LoanRow:
    snapshot = session.exec(
        select(LoanSnapshot).where(LoanSnapshot.debt_id == summary.debt_id).order_by(LoanSnapshot.as_of.desc())
    ).first()
    pct = None
    if summary.balance_known and summary.capital_granted:
        pct = max(0.0, min(100.0, summary.capital_remaining / summary.capital_granted * 100))
    alerts = session.exec(
        select(LoanAlert).where(LoanAlert.debt_id == summary.debt_id).order_by(LoanAlert.detected_on, LoanAlert.id)
    ).all()
    paid, premium, premium_date = _insurance_debits(session, summary.debt_id)
    debt = session.get(Debt, summary.debt_id)
    return LoanRow(
        summary=summary,
        insurance_paid=paid, last_premium=premium, last_premium_date=premium_date,
        alerts=[a for a in alerts if not a.acknowledged],
        groups=group_alerts([(a, debt) for a in alerts if not a.acknowledged]) if alerts else [],
        acked_alerts=[a for a in alerts if a.acknowledged],
        from_statements=snapshot is not None,
        rate_derived=summary.rate_source in ("derived", "debt"),
        remaining_pct=pct,
        payoff_text=payoff_text(summary.months_left, summary.payoff_date),
    )


@router.get("")
async def loans_index(request: Request, session: Session = Depends(get_session)):
    today = date.today()
    summaries = summarize_all(session, today)
    rows = [_loan_row(session, s) for s in summaries]
    active = [r for r in rows if r.summary.capital_remaining > 0 or not r.summary.balance_known]
    closed = [r for r in rows if r not in active]
    people = {p.id: p.name for p in session.exec(select(Person)).all()}
    informal = [
        {"debt": d, "balance": float(balance), "person": people.get(d.person_id), "summary": informal_summary(session, d)}
        for d, balance in informal_debt_rows(session)
    ]
    funds = savings_lines(session)
    holders: dict[str, list] = {}
    for line in funds:
        holders.setdefault(line.holder or "Other", []).append(line)
    deposits = newest_balances(session, "deposit")
    cards = newest_balances(session, "card")
    return templates.TemplateResponse(request, "loans/index.html", {
        "today": today,
        "alert_count": sum(len(r.groups) for r in rows),
        "totals": position_totals(session, today),
        "stale_after": STALE_AFTER_DAYS,
        "active_loans": active,
        "closed_loans": closed,
        "unknown_count": sum(1 for r in rows if not r.summary.balance_known),
        "holders": holders,
        "deposits": deposits,
        "cards": cards,
        "informal": informal,
        "today_iso": today.isoformat(),
        "upload_url": UPLOAD_URL,
        "empty": not (rows or funds or deposits or cards or informal),
    })


def _extraction_for(session: Session, document: Document) -> Optional[PositionExtraction]:
    session.expire_all()
    return session.exec(select(PositionExtraction).where(PositionExtraction.document_id == document.id)).first()


# --- messages for a file that could not be used (what / why / what to do) ---

_MB = 1024 * 1024
_RETRY_NEXT = "Try again. If it repeats, tell Pedro; the technical details below will help."
_CHECK_NEXT = "Check that this is the complete, right file, then upload it again."
# Text that is technical rather than a plain reason: never shown as the "why" itself.
_NOT_PLAIN = re.compile(r"Error|Exception|Traceback|https?://|[:{}\[\]]", re.I)
_CROPPED_NOTE = re.compile(r"oldest instalment nº (\d+) ignored")
_DISAGREE_NOTE = re.compile(r"(\d+) instalment\(s\) disagree with data already stored")


def _sentence(text: str) -> str:
    text = text.strip()
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def _explain(what: str, raw: Optional[str], actionable: Optional[str] = None) -> tuple[Problem, Optional[str]]:
    """(problem, technical). The reason in plain words is always the "why"; the raw stored text is only
    ever returned as `technical`, which the page shows under "Technical details"."""
    if not raw:
        return Problem(what, "The Hub stopped without recording why.", actionable or _RETRY_NEXT), None
    cause = classify_reason(raw)
    if cause is not None:
        return Problem(what, cause.why, actionable or cause.next_step), raw
    if len(raw.split()) >= 4 and not _NOT_PLAIN.search(raw):
        return Problem(what, _sentence(raw), actionable or _CHECK_NEXT), None
    return Problem(what, "An unexpected problem stopped the Hub while it was doing this.",
                   actionable or _RETRY_NEXT), raw


def _warnings(note: Optional[str]) -> list[Problem]:
    """A read that worked but left something out: what the stored note says, in plain words."""
    found = []
    for part in (note or "").split("; "):
        if m := _CROPPED_NOTE.search(part):
            found.append(Problem(
                f"Instalment nº {m[1]} was not recorded.",
                "The printout is cut off at its edge, so that instalment's amounts are incomplete.",
                "Upload a complete loan-history printout from Netbanco to record it."))
        elif m := _DISAGREE_NOTE.search(part):
            found.append(Problem(
                "Some instalments were not updated.",
                f"{m[1]} instalment(s) on this printout have different amounts from the ones already stored, "
                "so the stored figures were kept.",
                "Compare the two printouts with the bank to see which figures are right."))
        elif part.strip():
            found.append(_explain("Part of it was left out.", part)[0])
    return found


def _needs_loan(name: str, slot: str, extraction_id: Optional[int]) -> FileResult:
    return FileResult(name, slot, "needs_loan", extraction_id=extraction_id, problem=Problem(
        "Not assigned yet.", "This printout doesn't match any loan the Hub knows yet.",
        "Choose the loan it belongs to below, or enter a new loan's account number and name."))


def _not_pdf(name: str, problem: Optional[str]) -> Problem:
    if not name.lower().endswith(".pdf"):
        return Problem("Not uploaded.", "Only PDF files can be read, and this file isn't named as one.",
                       "Download it from the bank as a PDF, then upload that.")
    if problem == TRUNCATED:
        return Problem("Not uploaded.", "The file looks cut off: it doesn't end the way a complete PDF does.",
                       "Download it again from the bank, then upload it again.")
    return Problem("Not uploaded.", "The contents aren't a PDF, so they can't be read.",
                   "Download it from the bank as a PDF, then upload that.")


def _from_extraction(session: Session, name: str, slot: str, extraction: Optional[PositionExtraction],
                     document: Document, *, already: bool) -> FileResult:
    if extraction is None or extraction.status == "failed":
        raw = (extraction.error if extraction else None) or document.failure_reason
        problem, technical = _explain("Not read.", raw)
        return FileResult(name, slot, "failed", extraction_id=extraction.id if extraction else None,
                          problem=problem, technical=technical)
    if extraction.status == "needs_loan":
        result = _needs_loan(name, slot, extraction.id)
        result.warnings = _warnings(extraction.error)
        return result
    return FileResult(name, slot, "already_read" if already else "read", extraction_summary(session, extraction),
                      extraction.id, warnings=_warnings(extraction.error))


async def _handle_file(session: Session, request: Request, upload: UploadFile, slot_key: str) -> FileResult:
    slot = _SLOT[slot_key]
    name = upload.filename or "file"
    content = await upload.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        limit = f"{MAX_FILE_BYTES // _MB} MB" if MAX_FILE_BYTES >= _MB else f"{MAX_FILE_BYTES} bytes"
        return FileResult(name, slot["label"], "rejected", problem=Problem(
            "Not uploaded.", f"The file is larger than the {limit} limit for one file.",
            "Split it into smaller files (one month at a time), then upload them again."))
    clean, _, problem = inspect_pdf(content)
    if not name.lower().endswith(".pdf") or clean is None:
        return FileResult(name, slot["label"], "rejected", problem=_not_pdf(name, problem))
    content = clean  # a wrapped bank download is stored (and hashed) as the clean PDF
    label = slot["label"]
    try:
        received = await ingest(
            session,
            IncomingFile(name, content, DocumentSource.MANUAL, getattr(request.state, "user_email", None)),
            Classification(domain=Domain.FINANCIALS, category=slot["category"], fields={}),
        )
        document = received.document
        if not received.duplicate:
            return _from_extraction(session, name, label, _extraction_for(session, document), document, already=False)
        if document.category not in slot["compatible"]:
            shown = document.category or "an unfiled document"
            return FileResult(name, label, "other", problem=Problem(
                "Not read again.",
                f"This exact file was already uploaded as {shown}, so it isn't read again as a {label}.",
                "Nothing is lost. Upload the right printout for this box if this is a different one."))
        previous = _extraction_for(session, document)
        if previous is not None and previous.status == "ok":
            return _from_extraction(session, name, label, previous, document, already=True)
        # An ordinary `statement` that was already filed keeps its status whatever
        # the positions read does; only positions/history documents carry it.
        own = document.category in ("statement_positions", "loan_history")
        extraction = await slot["process"](session, document, mark_document=own)
        return _from_extraction(session, name, label, extraction, document, already=False)
    except InvalidClassification as exc:
        session.rollback()
        problem, technical = _explain("Not read.", "; ".join(exc.errors.values()), _CHECK_NEXT)
        return FileResult(name, label, "failed", problem=problem, technical=technical or str(exc))
    except Exception as exc:  # one bad file never aborts the rest
        log.exception("loan upload: %s failed", name)
        session.rollback()
        problem, technical = _explain("Not read.", str(exc) or exc.__class__.__name__)
        return FileResult(name, label, "failed", problem=problem, technical=technical)


def _existing_loans(session: Session) -> list[Debt]:
    return list(session.exec(select(Debt).where(Debt.external_number.isnot(None)).order_by(Debt.name)).all())


def _results_page(request: Request, session: Session, results: list[FileResult], error: Optional[Problem] = None,
                  status_code: int = 200):
    return templates.TemplateResponse(
        request, "loans/results.html",
        {"results": results, "loans": _existing_loans(session), "error": error}, status_code=status_code,
    )


@router.get("/upload")
async def upload_form(request: Request):
    return templates.TemplateResponse(request, "loans/upload.html", {})


def _uploaded(form, field: str) -> list[UploadFile]:
    """The real files of one form field. A browser sends an empty part (filename
    '') for an unfilled input, which the multipart parser hands over as a plain
    string: it is not a file, and declaring the field as list[UploadFile] would
    reject the whole form with a 422."""
    return [u for u in form.getlist(field) if isinstance(u, StarletteUploadFile) and u.filename]


@router.post("/upload")
async def upload_files(request: Request, session: Session = Depends(get_session)):
    # Both boxes are optional and independent; at least one file is required.
    form = await request.form()
    statements, histories = _uploaded(form, STATEMENTS), _uploaded(form, HISTORIES)
    if not statements and not histories:
        raise HTTPException(status_code=400, detail="Choose at least one file")
    if len(statements) + len(histories) > MAX_UPLOAD_FILES:
        raise HTTPException(status_code=400, detail=f"Upload at most {MAX_UPLOAD_FILES} files at a time")
    results = []
    for slot_key, uploads in ((STATEMENTS, statements), (HISTORIES, histories)):  # statements first
        for upload in uploads:
            results.append(await _handle_file(session, request, upload, slot_key))
    return _results_page(request, session, results)


@router.post("/assign")
async def assign_history(
    request: Request,
    extraction_id: int = Form(...),
    debt_id: str = Form("new"),
    number: str = Form(""),
    name: str = Form(""),
    loan_type: str = Form("mortgage"),
    session: Session = Depends(get_session),
):
    extraction = session.get(PositionExtraction, extraction_id)
    if extraction is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    if extraction.status != "needs_loan":
        raise HTTPException(status_code=400, detail="This printout is not waiting for a loan")
    document = session.get(Document, extraction.document_id)
    shown = document.filename if document else "printout"
    pending = _needs_loan(shown, _SLOT[HISTORIES]["label"], extraction.id)

    def reject(problem: Problem):
        return _results_page(request, session, [pending], error=problem, status_code=400)

    no_loan_picked = Problem(
        "Not assigned.", "The loan picked is no longer in the list (it may have been removed, or this page is out of date).",
        "Reload the page and choose the loan again.")
    if debt_id == "new":
        digits = re.sub(r"\s+", "", number)
        if not re.fullmatch(r"\d{8,}", digits):
            return reject(Problem(
                "Not assigned.", "An account number is made of at least 8 digits, with no letters or symbols.",
                "Copy the digits from the COB.REC.31 text of its bank transactions (spaces are fine), then assign again."))
        if not name.strip():
            return reject(Problem(
                "Not assigned.", "The loan needs a name, so it can be told apart from the others in the list.",
                "Type a name such as “Car loan”, then assign again."))
        if loan_type not in LOAN_TYPES:
            return reject(Problem(
                "Not assigned.", "The loan type isn't one of the three the Hub knows.",
                "Choose mortgage, personal or other, then assign again."))
        debt = session.exec(select(Debt).where(Debt.external_number == digits)).first()
        if debt is None:
            conflict = loan_number_conflict(session, digits)
            if conflict:
                return reject(Problem(
                    "Not assigned.", conflict,
                    "Choose that existing loan from the list if this printout is for it. Otherwise check the "
                    "account number on the printout and try again."))
            debt = create_loan_from_assignment(session, digits, name.strip(), loan_type)
    else:
        debt = session.get(Debt, int(debt_id)) if debt_id.isdigit() else None
        if debt is None or debt.external_number is None:
            return reject(no_loan_picked)
    try:
        assign_history_to_loan(session, extraction, debt)
    except ValueError as exc:
        session.rollback()
        problem, _ = _explain("Not assigned.", str(exc),
                              "Choose the loan this printout really belongs to from the list, or create a new loan "
                              "with its own account number.")
        return reject(problem)
    session.refresh(extraction)
    result = _from_extraction(session, shown, _SLOT[HISTORIES]["label"], extraction, document, already=False)
    return _results_page(request, session, [result])


def _safe_next(next_url: str) -> str:
    # `next` only ever points back into the loans pages (no open redirect).
    return next_url if next_url.startswith("/financials/loans") and not next_url.startswith("//") \
        and "://" not in next_url else "/financials/loans"


def _ack_note(note: str) -> str:
    if len(note or "") > MAX_ACK_NOTE:
        raise HTTPException(status_code=400, detail=f"The note is at most {MAX_ACK_NOTE} characters")
    return note


@router.post("/alerts/ack-group")
async def acknowledge_group_route(debt_id: int = Form(...), kind: str = Form(""), note: str = Form(""),
                                  next: str = Form(""), session: Session = Depends(get_session)):
    debt = session.get(Debt, debt_id)
    if debt is None or debt.external_number is None:
        raise HTTPException(status_code=404, detail="Loan not found")
    if kind not in ALERT_KINDS:
        raise HTTPException(status_code=400, detail="Unknown alert kind")
    acknowledge_group(session, debt_id, kind, _ack_note(note))
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/alerts/{alert_id}/ack")
async def acknowledge(alert_id: int, note: str = Form(""), next: str = Form(""),
                      session: Session = Depends(get_session)):
    _ack_note(note)
    try:
        acknowledge_alert(session, alert_id, note)
    except LookupError:
        raise HTTPException(status_code=404, detail="Alert not found")
    return RedirectResponse(_safe_next(next), status_code=303)


MAX_NAME_CHARS = 80
MAX_NOTE_CHARS = 300


def _bad(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def _parse_amount(raw: str, *, required: bool = True) -> Optional[float]:
    raw = (raw or "").strip().replace(",", ".")
    if not raw and not required:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise _bad("The amount must be a number")
    if not math.isfinite(value) or round(value, 2) <= 0:
        raise _bad("The amount must be greater than zero")
    return round(value, 2)


def _parse_date(raw: str, *, default: Optional[date] = None) -> date:
    raw = (raw or "").strip()
    if not raw and default is not None:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise _bad("The date must look like 2026-01-31")


def _informal_debt(session: Session, debt_id: int) -> Debt:
    debt = session.get(Debt, debt_id)
    if debt is None or debt.external_number is not None:
        raise HTTPException(status_code=404, detail="Informal loan not found")
    return debt


def _note(raw: str) -> Optional[str]:
    note = (raw or "").strip()
    if len(note) > MAX_NOTE_CHARS:
        raise _bad(f"The note is at most {MAX_NOTE_CHARS} characters")
    return note or None


@router.post("/debts/new")
async def new_informal_loan(
    person_name: str = Form(""), direction: str = Form(""), opening_amount: str = Form(""),
    entry_date: str = Form(""), note: str = Form(""), session: Session = Depends(get_session),
):
    name = person_name.strip()
    if not name:
        raise _bad("Give the person's name")
    if len(name) > MAX_NAME_CHARS:
        raise _bad(f"The name is at most {MAX_NAME_CHARS} characters")
    try:
        parsed_direction = DebtDirection(direction)
    except ValueError:
        raise _bad("Choose who owes whom")
    amount = _parse_amount(opening_amount, required=False)
    when = _parse_date(entry_date, default=date.today())
    text = _note(note)
    debt_ledger.create_informal_debt(session, name, parsed_direction, amount, when, text)
    session.commit()
    return RedirectResponse("/financials/loans", status_code=303)


@router.post("/debts/{debt_id}/entries")
async def add_debt_entry(
    debt_id: int, kind: str = Form(""), amount: str = Form(""), entry_date: str = Form(""),
    note: str = Form(""), session: Session = Depends(get_session),
):
    debt = _informal_debt(session, debt_id)
    if kind not in debt_ledger.KINDS:
        raise _bad("Choose advance, repayment or an adjustment")
    value = _parse_amount(amount)
    when = _parse_date(entry_date)
    text = _note(note)
    debt_ledger.add_entry(session, debt, kind, value, when, note=text)
    session.commit()
    return RedirectResponse("/financials/loans", status_code=303)


@router.post("/debts/{debt_id}/entries/{entry_id}/delete")
async def delete_debt_entry(debt_id: int, entry_id: int, session: Session = Depends(get_session)):
    _informal_debt(session, debt_id)
    entry = session.get(DebtEntry, entry_id)
    if entry is None or entry.debt_id != debt_id:
        raise HTTPException(status_code=404, detail="Entry not found")
    try:
        debt_ledger.delete_manual_entry(session, entry_id)
    except ValueError as exc:
        raise _bad(str(exc))
    session.commit()
    return RedirectResponse("/financials/loans", status_code=303)


@router.get("/{debt_id}")
async def loan_detail(debt_id: int, request: Request, session: Session = Depends(get_session)):
    debt = session.get(Debt, debt_id)
    if debt is None or debt.external_number is None:
        raise HTTPException(status_code=404, detail="Loan not found")
    movements = session.exec(
        select(LoanMovement).where(LoanMovement.debt_id == debt.id)
        .order_by(LoanMovement.movement_date.desc(), LoanMovement.instalment_number.desc())
    ).all()
    series = [m.balance_after for m in reversed(movements) if m.balance_after is not None]
    top = max(series) if series else 0
    strip = [{"value": v, "pct": round(v / top * 100) if top else 0} for v in series]
    snapshots = session.exec(
        select(LoanSnapshot).where(LoanSnapshot.debt_id == debt.id).order_by(LoanSnapshot.as_of)
    ).all()
    rate_history = [
        {"as_of": sn.as_of, "indexante": sn.indexante_percent, "spread": sn.spread_percent,
         "tan": sn.next_rate_percent if sn.next_rate_percent is not None else sn.rate_percent,
         "spread_changed": (sn.spread_percent is not None and debt.spread_percent is not None
                            and abs(sn.spread_percent - debt.spread_percent) > SPREAD_TOLERANCE)}
        for sn in snapshots
    ]
    return templates.TemplateResponse(request, "loans/detail.html", {
        "row": _loan_row(session, summarize_loan(session, debt, date.today())),
        "rate_history": rate_history,
        "baseline_spread": debt.spread_percent,
        "movements": movements,
        "strip": strip,
        "upload_url": UPLOAD_URL,
    })
