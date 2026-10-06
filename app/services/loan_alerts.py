"""Loan red flags. Interest-only instalments are NOT normal (a bank once charged
only interest for 15 months by error) and the spread over the indexante never
changes on its own: both are stored as data AND raised as alerts that stay
visible until acknowledged. Alerts are idempotent by (debt, kind, ref).

Kinds: interest_only (capital 0, interest > 0), spread_changed (a snapshot's
spread differs from the debt's baseline), rate_inconsistent (TAN != indexante +
spread). An instalment with neither capital nor interest (insurance-only) is
ignored."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from sqlmodel import Session, select

from app.models.debt import Debt
from app.models.document import Document
from app.models.position import LoanAlert, LoanMovement, LoanSnapshot
from app.services.position_extraction import Instalment

INTEREST_ONLY = "interest_only"
SPREAD_CHANGED = "spread_changed"
RATE_INCONSISTENT = "rate_inconsistent"

SPREAD_TOLERANCE = 0.001
RATE_TOLERANCE = 0.005


def _day(d: date) -> str:
    return d.strftime("%d %b %Y")


def _existing_keys(session: Session, debt: Debt) -> dict[tuple[str, str], LoanAlert]:
    return {(a.kind, a.ref): a for a in session.exec(select(LoanAlert).where(LoanAlert.debt_id == debt.id)).all()}


def _raise(session: Session, debt: Debt, kind: str, ref: str, message: str, document_id: Optional[int],
           keys: dict) -> int:
    if (kind, ref) in keys:
        return 0
    alert = LoanAlert(debt_id=debt.id, kind=kind, ref=ref, message=message, detected_on=date.today(),
                      document_id=document_id)
    session.add(alert)
    keys[(kind, ref)] = alert
    return 1


def detect_alerts_for_instalments(session: Session, debt: Debt, instalments: list[Instalment],
                                  document: Optional[Document]) -> int:
    """interest_only alerts for instalments with capital 0 and interest > 0. The
    stored instalment is described when one exists (it is what the app shows);
    returns how many alerts were created."""
    stored = {m.instalment_number: m
              for m in session.exec(select(LoanMovement).where(LoanMovement.debt_id == debt.id)).all()}
    keys = _existing_keys(session, debt)
    created = 0
    for inst in instalments:
        m = stored.get(inst.number)
        capital, interest, when = (m.capital, m.interest, m.movement_date) if m else (inst.capital, inst.interest, inst.date)
        if capital != 0 or interest <= 0:
            continue
        message = (f"Instalment {inst.number} ({_day(when)}): interest only, no capital repaid "
                   f"(€{interest:,.2f} interest)")
        created += _raise(session, debt, INTEREST_ONLY, f"inst:{inst.number}", message,
                          document.id if document is not None else None, keys)
    session.flush()
    return created


def detect_alerts_for_snapshot(session: Session, debt: Debt, snapshot: LoanSnapshot) -> int:
    """Baseline spread = the spread of the OLDEST snapshot that has one (recomputed
    when an older snapshot arrives out of order). Then every snapshot with a
    spread is checked against it, so the alert set is consistent whatever the
    order of arrival: missing spread_changed / rate_inconsistent alerts are
    created; an UNacknowledged spread_changed alert whose snapshot now equals the
    baseline is deleted (acknowledged ones are kept). Returns alerts created."""
    session.flush()
    snaps = session.exec(select(LoanSnapshot).where(LoanSnapshot.debt_id == debt.id)
                         .order_by(LoanSnapshot.as_of)).all()
    with_spread = [s for s in snaps if s.spread_percent is not None]
    if with_spread:
        baseline = with_spread[0].spread_percent
        if debt.spread_percent is None or abs(debt.spread_percent - baseline) > 1e-9:
            debt.spread_percent = baseline
            session.add(debt)
    baseline = debt.spread_percent
    keys = _existing_keys(session, debt)
    created = 0
    for s in snaps:
        ref = f"snap:{s.as_of.isoformat()}"
        if s.spread_percent is not None and baseline is not None:
            differs = abs(s.spread_percent - baseline) > SPREAD_TOLERANCE
            existing = keys.get((SPREAD_CHANGED, ref))
            if differs:
                message = (f"Spread changed {baseline:.3f}% → {s.spread_percent:.3f}% "
                           f"(statement of {_day(s.as_of)})")
                created += _raise(session, debt, SPREAD_CHANGED, ref, message, s.document_id, keys)
                if existing is not None and not existing.acknowledged and existing.message != message:
                    existing.message = message  # the baseline moved: keep old -> new accurate
                    session.add(existing)
            elif existing is not None and not existing.acknowledged:
                session.delete(existing)
                del keys[(SPREAD_CHANGED, ref)]
        tan = s.next_rate_percent  # only the NEXT rate is compared; never the current one
        if tan is not None and s.indexante_percent is not None and s.spread_percent is not None:
            expected = s.indexante_percent + s.spread_percent
            if abs(tan - expected) > RATE_TOLERANCE:
                message = (f"Rate inconsistent on {_day(s.as_of)}: TAN {tan:.3f}% is not indexante "
                           f"{s.indexante_percent:.3f}% + spread {s.spread_percent:.3f}% = {expected:.3f}%")
                created += _raise(session, debt, RATE_INCONSISTENT, ref, message, s.document_id, keys)
    session.flush()
    return created


def acknowledge_alert(session: Session, alert_id: int, note: Optional[str]) -> LoanAlert:
    """Mark an alert acknowledged (first acknowledgement and its note stand).
    Raises LookupError for an unknown id."""
    alert = session.get(LoanAlert, alert_id)
    if alert is None:
        raise LookupError(f"alert {alert_id} not found")
    if not alert.acknowledged:
        alert.acknowledged = True
        alert.ack_note = (note or "").strip() or None
        alert.acknowledged_at = datetime.utcnow()
        session.add(alert)
        session.commit()
        session.refresh(alert)
    return alert


def unacknowledged_alerts(session: Session) -> list[tuple[LoanAlert, Debt]]:
    """Every open alert with its loan, oldest detection first."""
    rows = session.exec(
        select(LoanAlert, Debt).where(LoanAlert.debt_id == Debt.id, LoanAlert.acknowledged.is_(False))
        .order_by(LoanAlert.detected_on, LoanAlert.id)
    ).all()
    return [(a, d) for a, d in rows]


MAX_NOTE_CHARS = 300
KINDS = (INTEREST_ONLY, SPREAD_CHANGED, RATE_INCONSISTENT)
_KIND_LABEL = {SPREAD_CHANGED: ("spread change", "spread changes"),
               RATE_INCONSISTENT: ("inconsistent rate", "inconsistent rates")}


@dataclass
class AlertGroup:
    """The alerts of one (loan, kind), for display. The individual alerts stay in the DB."""
    debt: Debt
    kind: str
    alerts: list = field(default_factory=list)
    text: str = ""


def _group_text(kind: str, alerts: list[LoanAlert]) -> str:
    if len(alerts) == 1:
        return alerts[0].message
    if kind == INTEREST_ONLY:
        numbers = sorted(int(a.ref.split(":")[1]) for a in alerts if a.ref.startswith("inst:") and a.ref[5:].isdigit())
        total = sum(float(m.group(1).replace(",", "")) for a in alerts
                    if (m := re.search(r"€([\d,]+\.\d\d) interest", a.message)))
        span = f"nº {numbers[0]}–{numbers[-1]}" if numbers else ""
        money = f"total interest €{total:,.2f}" if total else ""
        detail = ", ".join(x for x in (span, money) if x)
        return f"{len(alerts)} interest-only instalments" + (f" ({detail})" if detail else "")
    latest = max(alerts, key=lambda a: (a.detected_on, a.id))
    plural = _KIND_LABEL.get(kind, (kind, kind + "s"))[1]
    return f"{len(alerts)} {plural}; latest: {latest.message}"


def group_alerts(rows: list[tuple[LoanAlert, Debt]]) -> list[AlertGroup]:
    """Group alerts per (debt, kind); groups keep the order of their first alert."""
    groups: dict[tuple[int, str], AlertGroup] = {}
    for alert, debt in rows:
        group = groups.setdefault((debt.id, alert.kind), AlertGroup(debt=debt, kind=alert.kind))
        group.alerts.append(alert)
    for group in groups.values():
        group.text = _group_text(group.kind, group.alerts)
    return list(groups.values())


def unacknowledged_groups(session: Session) -> list[AlertGroup]:
    return group_alerts(unacknowledged_alerts(session))


def acknowledge_group(session: Session, debt_id: int, kind: str, note: Optional[str]) -> int:
    """Acknowledge every open alert of one (loan, kind) with the same note.
    Returns how many were acknowledged."""
    alerts = session.exec(select(LoanAlert).where(
        LoanAlert.debt_id == debt_id, LoanAlert.kind == kind, LoanAlert.acknowledged.is_(False))).all()
    for alert in alerts:
        alert.acknowledged = True
        alert.ack_note = (note or "").strip() or None
        alert.acknowledged_at = datetime.utcnow()
        session.add(alert)
    session.commit()
    return len(alerts)
