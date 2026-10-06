"""Loan insurance debits (SEG VIDA ..., SEG:EDF ..., SEG:LAR ...) tied to their loans.

The bank debits the life / building insurance of a loan separately from the
instalment, with a provider text that carries no loan number. A debit is tied
to its loan in two ways: a stored rule (normalized provider key -> loan and
component), or - the first time - a unique amount match against the
`insurance_life` / `insurance_building` of a loan movement paid around the same
day, which then creates the rule. Anything ambiguous stays unlinked.

These debits are ordinary monthly spend (filed under the Loan insurance
nodes), never loan instalments."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.models.debt import Debt
from app.models.position import LoanInsuranceRule, LoanMovement
from app.models.transaction import Transaction, TransactionType
from app.services.taxonomy import file_transaction, get_node

LIFE_SLUG = "loans-debt.loan-insurance.life-insurance-loan"
BUILDING_SLUG = "loans-debt.loan-insurance.building-insurance-loan"
_SLUG = {"life": LIFE_SLUG, "building": BUILDING_SLUG}

WINDOW_DAYS = 7
AMOUNT_TOLERANCE = 0.01

_SEG = re.compile(r"^seg[:\s]", re.IGNORECASE)
# Date fragments: an ISO range (2026-09-02/2026-10-01), a slashed date
# (-2026/08/21, 2026/08/21) or a standalone ISO date, with the separators in front.
_DATE = r"\d{4}[-/]\d{2}[-/]\d{2}"
_DATE_FRAGMENT = re.compile(rf"[\s\-/]*(?<!\d){_DATE}(?:\s*/\s*{_DATE})?(?!\d)")


def insurance_key(provider_text: Optional[str]) -> Optional[str]:
    """Stable key of an insurance provider text: lowercased, date fragments
    removed, whitespace collapsed. None unless the text starts with `seg`
    followed by ':' or a space (so SEGURANCA SOCIAL is not an insurance); ':' and
    a space give the same key (SEG:EDF == SEG EDF -> "seg edf")."""
    text = (provider_text or "").strip()
    if not _SEG.match(text):
        return None
    text = _DATE_FRAGMENT.sub(" ", text.lower().replace(":", " "))
    text = re.sub(r"\s+", " ", text).strip(" -/")
    return text if _SEG.match(text + " ") and len(text) > 4 else None


@dataclass
class InsuranceIndex:
    """What matching needs, loaded once for a whole batch."""
    debt_ids: set[int]
    # (debt_id, movement_date, insurance_life, insurance_building)
    movements: list[tuple[int, date, float, float]] = field(default_factory=list)
    rules: dict[str, LoanInsuranceRule] = field(default_factory=dict)


def load_index(session: Session, loans: Optional[list[Debt]] = None) -> InsuranceIndex:
    if loans is None:
        loans = list(session.exec(select(Debt).where(Debt.external_number.isnot(None))).all())
    debt_ids = {d.id for d in loans}
    movements = [
        (m.debt_id, m.movement_date, m.insurance_life, m.insurance_building)
        for m in session.exec(select(LoanMovement).where(
            (LoanMovement.insurance_life > 0) | (LoanMovement.insurance_building > 0))).all()
        if m.debt_id in debt_ids
    ]
    rules = {r.normalized_key: r for r in session.exec(select(LoanInsuranceRule)).all()}
    return InsuranceIndex(debt_ids, movements, rules)


# The only provider families that are loan insurance, and the component each one is.
_FAMILIES = (("seg vida", "life"), ("seg edf", "building"), ("seg lar", "building"))


def key_component(key: str) -> Optional[str]:
    for prefix, component in _FAMILIES:
        if key == prefix or key.startswith(prefix + " "):
            return component
    return None


def _amount_candidates(index: InsuranceIndex, txn: Transaction, component: str) -> set[tuple[int, str]]:
    if txn.paid_date is None:
        return set()
    found: set[tuple[int, str]] = set()
    for debt_id, moved, life, building in index.movements:
        if abs((moved - txn.paid_date).days) > WINDOW_DAYS:
            continue
        amount = life if component == "life" else building
        if amount > 0 and round(abs(amount - txn.amount), 2) <= AMOUNT_TOLERANCE:
            found.add((debt_id, component))
    return found


def link_insurance_transaction(session: Session, txn: Transaction, index: Optional[InsuranceIndex] = None) -> bool:
    """Link one `seg...` debit to its loan and file it under the matching
    insurance node. Never overwrites an existing debt_id; idempotent; does not
    commit. `index` lets a batch caller load loans, movements and rules once."""
    if txn.debt_id is not None or txn.transaction_type != TransactionType.DEBIT:
        return False
    key = insurance_key(txn.provider)
    family = key_component(key) if key is not None else None
    if family is None:  # only SEG VIDA / SEG EDF / SEG LAR are loan insurance
        return False
    if index is None:
        index = load_index(session)
    rule = index.rules.get(key)
    if rule is not None:
        if rule.debt_id not in index.debt_ids or rule.component != family:
            return False  # the rule points at something that is no longer a statement loan
        debt_id, component, new_rule = rule.debt_id, rule.component, None
    else:
        found = _amount_candidates(index, txn, family)
        if len(found) != 1:  # nothing, or several loans / components: stay unlinked, no rule
            return False
        debt_id, component = next(iter(found))
        new_rule = LoanInsuranceRule(debt_id=debt_id, component=component, normalized_key=key)
    node = get_node(session, _SLUG[component])  # look everything up before touching the row
    if new_rule is not None:
        session.add(new_rule)
        index.rules[key] = new_rule
    txn.debt_id = debt_id
    txn.debt_candidate_reviewed = True
    file_transaction(session, txn, node)
    session.add(txn)
    return True
