"""Stores extracted loan/savings positions and loan histories idempotently.
Every write is an upsert keyed on natural keys; nothing is ever overwritten
silently (disagreements are counted as conflicts)."""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import update
from sqlmodel import Session, select

from app.models.debt import Debt, DebtDirection, DebtKind
from app.models.document import Document, DocumentStatus
from app.models.position import (
    BalanceSnapshot, LoanAlert, LoanMovement, LoanSnapshot, PositionExtraction, SavingsSnapshot,
)
from app.services.loan_alerts import detect_alerts_for_instalments, detect_alerts_for_snapshot
from app.services.loan_linking import link_all_unlinked
from app.services.loan_math import effective_rate
from app.services.ingestion import mark_needs_attention
from app.services.position_extraction import (
    ComponentRow, ExtractedBalance, ExtractedFund, ExtractedLoan, ExtractedPositions, Instalment, aggregate_components, extract_loan_history,
    extract_statement_positions, reconcile, reconcile_loan,
)

log = logging.getLogger(__name__)

TOLERANCE = 0.02


def _backlink(session: Session) -> None:
    """Link existing instalment rows to loans; a failure never fails the document."""
    try:
        link_all_unlinked(session)
    except Exception:
        # The positions are already committed. A failed flush leaves the session
        # needing a rollback, without which the very next commit would raise and
        # a good upload would be reported as failed.
        session.rollback()
        log.exception("loan back-linking failed")


def _alert_step(session: Session, label: str, step) -> None:
    """Run one red-flag detection step on already-committed data. A failure never
    fails the document: it is logged and only that step is rolled back."""
    try:
        step()
        session.commit()
    except Exception:
        session.rollback()
        log.exception("loan alert detection failed (%s)", label)


def _digits_only(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _money(value: float) -> Decimal:
    return Decimal(str(round(value, 2)))


def _new_counts() -> dict:
    return {"loans": 0, "movements": 0, "snapshots": 0, "funds": 0, "balances": 0, "conflicts": 0}


def _latest_recorded(session: Session, debt: Debt) -> Optional[date]:
    dates = [s.as_of for s in session.exec(select(LoanSnapshot).where(LoanSnapshot.debt_id == debt.id)).all()]
    dates += [m.movement_date for m in session.exec(select(LoanMovement).where(LoanMovement.debt_id == debt.id)).all()]
    return max(dates) if dates else None


def _agree(existing: LoanMovement, capital: float, interest: float) -> bool:
    return abs(existing.capital - capital) <= TOLERANCE and abs(existing.interest - interest) <= TOLERANCE


def detach_positions(session: Session, document_id: int) -> None:
    """Registered data is never discarded: when a document goes away (withdrawn,
    re-filed, deleted) every position row it supplied keeps living with
    `document_id = NULL`. The single place that does it; does not commit."""
    for model in (LoanMovement, LoanSnapshot, SavingsSnapshot, BalanceSnapshot, LoanAlert):
        session.execute(update(model).where(model.document_id == document_id).values(document_id=None))


def _reattach(row, document: Document) -> None:
    """A row detached earlier belongs to the document that now describes it again."""
    if row.document_id is None:
        row.document_id = document.id


def _upsert_movement(session: Session, document: Document, debt: Debt, inst: Instalment, counts: dict) -> None:
    existing = session.exec(
        select(LoanMovement).where(LoanMovement.debt_id == debt.id, LoanMovement.instalment_number == inst.number)
    ).first()
    if existing is None:
        session.add(LoanMovement(
            debt_id=debt.id, instalment_number=inst.number, movement_date=inst.date, capital=inst.capital,
            interest=inst.interest, insurance=inst.insurance, insurance_life=inst.insurance_life,
            insurance_building=inst.insurance_building, balance_after=inst.balance_after,
            document_id=document.id,
        ))
        session.flush()
        counts["movements"] += 1
        return
    if not _agree(existing, inst.capital, inst.interest):
        counts["conflicts"] += 1
        return
    _reattach(existing, document)
    if not existing.insurance and inst.insurance:
        existing.insurance = inst.insurance
    if not existing.insurance_life and inst.insurance_life:
        existing.insurance_life = inst.insurance_life
    if not existing.insurance_building and inst.insurance_building:
        existing.insurance_building = inst.insurance_building
    if existing.balance_after is None and inst.balance_after is not None:
        existing.balance_after = inst.balance_after
    session.add(existing)


def _loan_type(label: str) -> str:
    upper = (label or "").upper()
    if "HABITA" in upper:
        return "mortgage"
    if "EMPR" in upper or "PESSOAL" in upper:
        return "personal"
    return "other"


MIN_LOAN_DIGITS = 8


def loan_number_conflict(session: Session, digits: str) -> Optional[str]:
    """Why `digits` cannot be a NEW loan's number: too short, or contained in /
    containing another loan's number (an exact match is the same loan, not a
    conflict). None when fine."""
    if len(digits) < MIN_LOAN_DIGITS:
        return f"loan number {digits!r} has fewer than {MIN_LOAN_DIGITS} digits"
    for other in session.exec(select(Debt.external_number).where(Debt.external_number.isnot(None))).all():
        if other != digits and (other in digits or digits in other):
            return f"loan number {digits} overlaps the existing loan number {other}"
    return None


def apply_positions(session: Session, document: Document, positions: ExtractedPositions) -> dict:
    for loan in positions.loans:  # validate everything before writing anything
        if len(loan.number) < MIN_LOAN_DIGITS:
            raise ValueError(f"loan number {loan.number!r} has fewer than {MIN_LOAN_DIGITS} digits")
        if session.exec(select(Debt).where(Debt.external_number == loan.number)).first() is None:
            problem = loan_number_conflict(session, loan.number)
            if problem:
                raise ValueError(problem)
    counts = _new_counts()
    alert_work: list[tuple[int, date, list[Instalment]]] = []
    for loan in positions.loans:
        debt = session.exec(select(Debt).where(Debt.external_number == loan.number)).first()
        if debt is None:
            original = loan.capital_granted or loan.capital_remaining
            debt = Debt(
                kind=DebtKind.FORMAL, direction=DebtDirection.OWED_BY_US, original_amount=original,
                current_balance=_money(loan.capital_remaining), interest_rate=loan.rate_percent,
                name=f"{loan.label} …{loan.number[-4:]}", external_number=loan.number,
                capital_granted=loan.capital_granted, term_months=loan.term_months,
                start_date=loan.start_date, loan_type=_loan_type(loan.label),
            )
            session.add(debt)
            session.flush()
            counts["loans"] += 1
        # A later statement fills what an earlier source left empty; never overwrites.
        if not debt.name:
            debt.name = f"{loan.label} …{loan.number[-4:]}"
        for attr in ("capital_granted", "term_months", "start_date"):
            if getattr(debt, attr) is None and getattr(loan, attr) is not None:
                setattr(debt, attr, getattr(loan, attr))
        session.add(debt)
        latest = _latest_recorded(session, debt)
        newest = latest is None or positions.as_of > latest

        snapshot = session.exec(
            select(LoanSnapshot).where(LoanSnapshot.debt_id == debt.id, LoanSnapshot.as_of == positions.as_of)
        ).first()
        if snapshot is None:
            session.add(LoanSnapshot(
                debt_id=debt.id, as_of=positions.as_of, capital_remaining=loan.capital_remaining,
                rate_percent=loan.rate_percent, next_rate_percent=loan.next_rate_percent, next_due_date=loan.next_due_date,
                next_instalment=loan.next_instalment, next_capital=loan.next_capital,
                next_interest=loan.next_interest, indexante_percent=loan.next_indexante_percent,
                spread_percent=loan.next_spread_percent, document_id=document.id,
            ))
            counts["snapshots"] += 1
        else:  # a re-read fills what an earlier read of the same snapshot left empty
            _reattach(snapshot, document)
            if snapshot.indexante_percent is None and loan.next_indexante_percent is not None:
                snapshot.indexante_percent = loan.next_indexante_percent
            if snapshot.spread_percent is None and loan.next_spread_percent is not None:
                snapshot.spread_percent = loan.next_spread_percent
            session.add(snapshot)
        if newest:
            debt.current_balance = _money(loan.capital_remaining)
            rate, _ = effective_rate(loan.next_rate_percent, loan.rate_percent, loan.next_interest, loan.capital_remaining)
            if rate is not None:
                debt.interest_rate = rate
            if loan.capital_remaining == 0:
                debt.status = "closed"
            elif debt.status == "closed":
                debt.status = "active"
            session.add(debt)
        loan_instalments = aggregate_components(loan.rows)
        for inst in loan_instalments:
            _upsert_movement(session, document, debt, inst, counts)
        alert_work.append((debt.id, positions.as_of, loan_instalments))

    for fund in positions.funds:
        saved = session.exec(select(SavingsSnapshot).where(
            SavingsSnapshot.account_ref == fund.account_ref, SavingsSnapshot.label == fund.label,
            SavingsSnapshot.as_of == positions.as_of,
        )).first()
        if saved is None:
            session.add(SavingsSnapshot(
                as_of=positions.as_of, holder=fund.holder, label=fund.label, account_ref=fund.account_ref,
                units=fund.units, invested=fund.invested, value=fund.value,
                periodic_amount=fund.periodic_amount, next_periodic_date=fund.next_periodic_date,
                document_id=document.id,
            ))
            counts["funds"] += 1
        else:
            _reattach(saved, document)
            session.add(saved)
    for bal in positions.balances:
        saved_bal = session.exec(select(BalanceSnapshot).where(
            BalanceSnapshot.kind == bal.kind, BalanceSnapshot.label == bal.label,
            BalanceSnapshot.as_of == positions.as_of,
        )).first()
        if saved_bal is None:
            session.add(BalanceSnapshot(
                as_of=positions.as_of, kind=bal.kind, label=bal.label, amount=bal.amount,
                document_id=document.id,
            ))
            counts["balances"] += 1
        else:
            _reattach(saved_bal, document)
            session.add(saved_bal)
    session.commit()
    for debt_id, as_of, loan_instalments in alert_work:
        def detect(debt_id=debt_id, as_of=as_of, loan_instalments=loan_instalments):
            debt = session.get(Debt, debt_id)
            snap = session.exec(
                select(LoanSnapshot).where(LoanSnapshot.debt_id == debt_id, LoanSnapshot.as_of == as_of)
            ).first()
            if snap is not None:
                detect_alerts_for_snapshot(session, debt, snap)
            detect_alerts_for_instalments(session, debt, loan_instalments, document)
        _alert_step(session, f"statement positions, debt {debt_id}", detect)
    return counts


def apply_loan_history(session: Session, document: Document, debt: Debt, instalments: list[Instalment]) -> dict:
    counts = _new_counts()
    latest = _latest_recorded(session, debt)
    for inst in instalments:
        _upsert_movement(session, document, debt, inst, counts)
    if instalments:
        ordered = sorted(instalments, key=lambda i: i.number)
        newest = ordered[-1]
        if latest is None or newest.date > latest:
            # An interest-only newest instalment leaves the balance unchanged,
            # so the latest known balance_after is still right. If NO instalment
            # carries a balance_after (e.g. an interest-only history) the balance
            # stays untouched: for a loan created from a history that is 0 with
            # original_amount 0, which later screens must read as "balance unknown".
            with_balance = [i for i in ordered if i.balance_after is not None]
            if with_balance:
                debt.current_balance = _money(with_balance[-1].balance_after)
                if with_balance[-1].balance_after == 0:
                    debt.status = "closed"  # paid down to zero
        if not debt.original_amount:
            first = next((i for i in ordered if i.balance_after is not None and i.capital > 0), None)
            if first is not None:
                debt.original_amount = round(first.balance_after + first.capital, 2)
        if debt.interest_rate is None:
            for inst in reversed(ordered):
                if inst.capital > 0 and inst.balance_after is not None:
                    debt.interest_rate = round(inst.interest * 12 / (inst.balance_after + inst.capital) * 100, 3)
                    break
        session.add(debt)
    session.commit()
    debt_id = debt.id
    _alert_step(session, f"loan history, debt {debt_id}",
                lambda: detect_alerts_for_instalments(session, session.get(Debt, debt_id), instalments, document))
    return counts


def match_loan_for_history(session: Session, instalments: list[Instalment]) -> Optional[Debt]:
    matches = []
    for debt in session.exec(select(Debt)).all():
        existing = {
            m.instalment_number: m
            for m in session.exec(select(LoanMovement).where(LoanMovement.debt_id == debt.id)).all()
        }
        agreeing = disagreeing = 0
        for inst in instalments:
            movement = existing.get(inst.number)
            if movement is None:
                continue
            if _agree(movement, inst.capital, inst.interest):
                agreeing += 1
            else:
                disagreeing += 1
        if agreeing >= 2 and disagreeing == 0:
            matches.append(debt)
    return matches[0] if len(matches) == 1 else None


def _instalments_to_list(instalments: list[Instalment]) -> list[dict]:
    return [
        {"number": i.number, "date": i.date.isoformat(), "capital": i.capital, "interest": i.interest,
         "insurance": i.insurance, "insurance_life": i.insurance_life,
         "insurance_building": i.insurance_building, "balance_after": i.balance_after}
        for i in instalments
    ]


def _instalments_from_list(items: list[dict]) -> list[Instalment]:
    return [
        Instalment(
            number=d["number"], date=date.fromisoformat(d["date"]), capital=d["capital"],
            interest=d["interest"], insurance=d.get("insurance") or 0.0, balance_after=d.get("balance_after"),
            insurance_life=d.get("insurance_life") or 0.0, insurance_building=d.get("insurance_building") or 0.0,
        )
        for d in items
    ]


def _dump_instalments(instalments: list[Instalment]) -> str:
    """needs_loan payload: the bare instalment list."""
    return json.dumps(_instalments_to_list(instalments))


def _dump_history(debt: Debt, instalments: list[Instalment]) -> str:
    """ok payload of a history: the matched loan plus the instalments."""
    return json.dumps({"debt_id": debt.id, "instalments": _instalments_to_list(instalments)})


def _load_history(payload: str) -> tuple[Optional[int], list[Instalment]]:
    data = json.loads(payload)
    if isinstance(data, list):
        return None, _instalments_from_list(data)
    return data.get("debt_id"), _instalments_from_list(data["instalments"])


def _dump_positions(positions: ExtractedPositions) -> str:
    return json.dumps(dataclasses.asdict(positions), default=lambda o: o.isoformat())


def _d(value) -> Optional[date]:
    return date.fromisoformat(value) if value else None


def _load_positions(payload: str) -> ExtractedPositions:
    data = json.loads(payload)
    loans = []
    for item in data["loans"]:
        item = dict(item)
        item["rows"] = [ComponentRow(**{**r, "date": date.fromisoformat(r["date"])}) for r in item["rows"]]
        for key in ("start_date", "next_due_date"):
            item[key] = _d(item[key])
        loans.append(ExtractedLoan(**item))
    funds = [ExtractedFund(**{**f, "next_periodic_date": _d(f["next_periodic_date"])}) for f in data["funds"]]
    return ExtractedPositions(
        as_of=date.fromisoformat(data["as_of"]), loans=loans, funds=funds,
        balances=[ExtractedBalance(**b) for b in data["balances"]], warnings=list(data.get("warnings", [])),
    )


def _conflict_note(counts: dict) -> Optional[str]:
    if counts.get("conflicts"):
        return f"{counts['conflicts']} instalment(s) disagree with data already stored"
    return None


def _join_notes(*notes: Optional[str]) -> Optional[str]:
    return "; ".join(n for n in notes if n) or None


def _store(session: Session, document: Document, status: str, error: Optional[str] = None,
           payload: Optional[str] = None) -> PositionExtraction:
    row = session.exec(select(PositionExtraction).where(PositionExtraction.document_id == document.id)).first()
    if row is None:
        row = PositionExtraction(document_id=document.id, status=status)
    row.status, row.error, row.payload_json = status, error, payload
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _fail(session: Session, document: Document, reason: str, mark_document: bool = True) -> PositionExtraction:
    """Record a failed extraction. With mark_document=False (the document is an
    ordinary statement that was already filed) the failure lives on the
    PositionExtraction row only and the document's status is left alone."""
    session.rollback()
    row = _store(session, document, "failed", error=reason)
    if mark_document:
        mark_needs_attention(session, document, reason)
    return row


def _mark_processed(session: Session, document: Document) -> None:
    document.status = DocumentStatus.PROCESSED
    document.failure_reason = None
    session.add(document)
    session.commit()


def _existing_ok(session: Session, document: Document) -> Optional[PositionExtraction]:
    row = session.exec(select(PositionExtraction).where(PositionExtraction.document_id == document.id)).first()
    return row if row is not None and row.status == "ok" else None


async def process_positions_document(session: Session, document: Document, gateway=None,
                                     mark_document: bool = True) -> PositionExtraction:
    """mark_document=False: the document is an already-filed ordinary statement;
    its status is never touched, success or failure."""
    existing = _existing_ok(session, document)
    if existing is not None:
        return existing
    try:
        positions = await extract_statement_positions(document.file_path, gateway=gateway)
        problems = reconcile(positions)
        if problems:
            return _fail(session, document, "; ".join(problems), mark_document)
        counts = apply_positions(session, document, positions)
    except Exception as exc:  # broad by design: any failure lands on needs_attention with a reason
        return _fail(session, document, str(exc), mark_document)
    _backlink(session)
    row = _store(session, document, "ok", error=_conflict_note(counts), payload=_dump_positions(positions))
    if mark_document:
        _mark_processed(session, document)
    return row


async def process_loan_history_document(session: Session, document: Document, gateway=None,
                                        mark_document: bool = True) -> PositionExtraction:
    existing = _existing_ok(session, document)
    if existing is not None:
        return existing
    try:
        history = await extract_loan_history(document.file_path, gateway=gateway)
        if not history.rows:
            return _fail(session, document, "no loan movements found in the document", mark_document)
        rows, crop_note = history.rows, None
        numbers = {r.instalment_number for r in rows}
        if not history.complete and len(numbers) > 1:
            # printouts list newest first and may be cut mid-instalment at the bottom
            oldest = min(numbers)
            rows = [r for r in rows if r.instalment_number != oldest]
            crop_note = f"oldest instalment nº {oldest} ignored: printout is cropped"
        problems = reconcile_loan(rows)
        if problems:
            return _fail(session, document, "; ".join(problems), mark_document)
        instalments = aggregate_components(rows)
        debt = match_loan_for_history(session, instalments)
        if debt is None:
            row = _store(session, document, "needs_loan", error=crop_note, payload=_dump_instalments(instalments))
            if mark_document:
                _mark_processed(session, document)
            return row
        counts = apply_loan_history(session, document, debt, instalments)
    except Exception as exc:
        return _fail(session, document, str(exc), mark_document)
    _backlink(session)
    row = _store(session, document, "ok", error=_join_notes(crop_note, _conflict_note(counts)),
                 payload=_dump_history(debt, instalments))
    if mark_document:
        _mark_processed(session, document)
    return row


def assign_history_to_loan(session: Session, extraction: PositionExtraction, debt: Debt) -> dict:
    document = session.get(Document, extraction.document_id)
    _, instalments = _load_history(extraction.payload_json or "[]")
    existing = {
        m.instalment_number: m
        for m in session.exec(select(LoanMovement).where(LoanMovement.debt_id == debt.id)).all()
    }
    for inst in instalments:
        movement = existing.get(inst.number)
        if movement is not None and not _agree(movement, inst.capital, inst.interest):
            raise ValueError(
                f"instalment {inst.number} of this printout has different amounts from instalment "
                f"{inst.number} already recorded for this loan - it looks like another loan"
            )
    counts = apply_loan_history(session, document, debt, instalments)
    crop = (extraction.error or "").split("; ")[0]
    crop = crop if crop.startswith("oldest instalment") else None
    extraction.status, extraction.error = "ok", _join_notes(crop, _conflict_note(counts))
    extraction.payload_json = _dump_history(debt, instalments)
    session.add(extraction)
    session.commit()
    _backlink(session)
    return counts


def create_loan_from_assignment(session: Session, number: str, name: str, loan_type: str) -> Debt:
    """A manual formal loan; original_amount is filled in when its history is assigned."""
    debt = Debt(
        kind=DebtKind.FORMAL, direction=DebtDirection.OWED_BY_US, original_amount=0.0,
        current_balance=Decimal("0.00"), interest_rate=None, name=name,
        external_number=_digits_only(number), loan_type=loan_type,
    )
    session.add(debt)
    session.commit()
    session.refresh(debt)
    return debt


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def extraction_summary(session: Session, extraction: PositionExtraction) -> str:
    """One human line describing what an extraction holds ("1 loan, 2 instalments, 0 funds")."""
    if not extraction.payload_json:
        return ""
    data = json.loads(extraction.payload_json)
    if isinstance(data, dict) and "loans" in data:
        positions = _load_positions(extraction.payload_json)
        instalments = sum(len({r.instalment_number for r in loan.rows}) for loan in positions.loans)
        return (f"{_plural(len(positions.loans), 'loan')}, {_plural(instalments, 'instalment')}, "
                f"{_plural(len(positions.funds), 'fund')}")
    debt_id, instalments = _load_history(extraction.payload_json)
    debt = session.get(Debt, debt_id) if debt_id else None
    return f"{_plural(len(instalments), 'instalment')}" + (f" for {debt.name}" if debt else "")
