"""Tier wiring as configuration (decision D12): reads and writes the
document_type_settings table. No tier is hard-coded anywhere else.

Three states for a document type:
- enforcement OFF  -> "off": behaves as before this feature existed.
- enforcement ON, tier set        -> "ready" (with its tier and redaction switch).
- enforcement ON, tier not set    -> "held": nothing may read the document yet.
Turning enforcement on is a deliberate act on the settings screen."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from app.domains.registry import implemented_domains
from app.models.document import Document
from app.models.domain import Domain
from app.models.privacy import DocumentTypeSetting, PrivacySetting, TierChange
from app.models.record import Record
from app.privacy.tiers import Tier, TierBlocked, parse_tier

ENFORCE_KEY = "enforce_tiers"

# Starting suggestions shown pre-filled on the settings screen (Pedro, 2026-10-15).
# They are NOT active until confirmed. house_high is deliberately never suggested:
# Pedro chooses it per type (it is served only by Privatemode, which costs money).
SUGGESTED_TIERS: dict[tuple[str, str], Tier] = {
    ("financials", "bill"): Tier.MID,
    ("financials", "statement"): Tier.MID,
    ("financials", "statement_positions"): Tier.MID,
    ("financials", "loan_history"): Tier.MID,
    ("house", "house_appliance"): Tier.LOW,
    ("house", "outdoor_gear"): Tier.LOW,
    ("house", "warranty_invoice"): Tier.LOW,
    ("house", "maintenance_log"): Tier.LOW,
    ("house", "floor_plan"): Tier.LOW,
    ("house", "ownership_document"): Tier.MID,
    ("insurance", "home_insurance"): Tier.MID,
    ("insurance", "car_insurance"): Tier.MID,
    ("insurance", "health_insurance"): Tier.MID,
    ("insurance", "life_insurance"): Tier.MID,
}
FALLBACK_SUGGESTION = Tier.MID      # a type added later: cautious middle until Pedro decides


class TierHeld(Exception):
    """Enforcement is on and this document type has no tier yet: the document waits."""

    def __init__(self, domain: object, category: object):
        self.domain, self.category = _s(domain), category
        super().__init__(f"no tier set for {self.domain}/{category}")


@dataclass(frozen=True)
class TypeInfo:
    domain: str
    domain_label: str
    category: str
    label: str
    description: str
    suggested: Tier


@dataclass(frozen=True)
class TypeRow:
    info: TypeInfo
    tier: Optional[Tier]              # saved value, None = not set
    redact: bool
    shown_tier: Tier                  # what the dropdown shows: saved, else suggestion
    is_saved: bool


@dataclass(frozen=True)
class Resolution:
    state: str                        # "off" | "ready" | "held"
    tier: Optional[Tier] = None
    redact: bool = False


def _s(value: object) -> str:
    return str(getattr(value, "value", value))


def known_types() -> list[TypeInfo]:
    out = []
    for spec in implemented_domains():
        for c in spec.categories:
            key = (spec.domain.value, c.value)
            out.append(TypeInfo(spec.domain.value, spec.label, c.value, c.label, c.description,
                                SUGGESTED_TIERS.get(key, FALLBACK_SUGGESTION)))
    return out


def _setting(session: Session, domain: object, category: str) -> Optional[DocumentTypeSetting]:
    return session.exec(select(DocumentTypeSetting).where(
        DocumentTypeSetting.domain == _s(domain), DocumentTypeSetting.category == category)).first()


def configured_tier(session: Session, domain: object, category: Optional[str]) -> Optional[str]:
    """The tier saved for a type, whether or not levels are binding yet (None = not set)."""
    if domain is None or category is None:
        return None
    row = _setting(session, domain, category)
    return row.tier if row is not None else None


def enforcing(session: Session) -> bool:
    row = session.get(PrivacySetting, ENFORCE_KEY)
    return row is not None and row.value == "1"


def resolve(session: Session, domain: object, category: Optional[str]) -> Resolution:
    if not enforcing(session):
        return Resolution("off")
    row = _setting(session, domain, category) if domain is not None and category is not None else None
    if row is None or row.tier is None:
        return Resolution("held")
    return Resolution("ready", parse_tier(row.tier), row.redact)


def require_ready(session: Session, domain: object, category: Optional[str]) -> Resolution:
    """The gate before anything reads a document: raises TierHeld while the type has no tier."""
    res = resolve(session, domain, category)
    if res.state == "held":
        raise TierHeld(domain, category)
    return res


def type_rows(session: Session) -> list[TypeRow]:
    saved = {(r.domain, r.category): r for r in session.exec(select(DocumentTypeSetting))}
    rows = []
    for info in known_types():
        row = saved.get((info.domain, info.category))
        tier = parse_tier(row.tier) if row is not None and row.tier else None
        rows.append(TypeRow(info, tier, bool(row and row.redact), tier or info.suggested, row is not None and tier is not None))
    return rows


def _log(session, *, domain, category, what, old, new, by) -> None:
    session.add(TierChange(domain=domain, category=category, what=what, old_value=old, new_value=new,
                           changed_by=by, changed_at=datetime.utcnow()))


def save_type(session: Session, domain: str, category: str, tier: object, redact: bool, by: Optional[str]) -> None:
    """Set a type's tier (a tier value, or "" / None to unset) and redaction switch. Logged;
    documents and records already filed under the type are re-tiered at once."""
    if (domain, category) not in {(i.domain, i.category) for i in known_types()}:
        raise TierBlocked(f"unknown document type {domain}/{category}")
    new_tier = parse_tier(tier).value if tier not in (None, "") else None
    row = _setting(session, domain, category)
    old_tier, old_redact = (row.tier, row.redact) if row else (None, False)
    if row is None:
        row = DocumentTypeSetting(domain=domain, category=category)
    row.tier, row.redact, row.updated_by, row.updated_at = new_tier, bool(redact), by, datetime.utcnow()
    session.add(row)
    if old_tier != new_tier:
        _log(session, domain=domain, category=category, what="tier", old=old_tier, new=new_tier, by=by)
    if old_redact != bool(redact):
        _log(session, domain=domain, category=category, what="redact", old=str(old_redact), new=str(bool(redact)), by=by)
    session.commit()
    retier(session, domain, category, new_tier)


def confirm_suggestions(session: Session, by: Optional[str]) -> int:
    """Save the suggested tier for every type that has none yet. Returns how many."""
    n = 0
    for r in type_rows(session):
        if r.tier is None:
            save_type(session, r.info.domain, r.info.category, r.info.suggested, r.redact, by)
            n += 1
    return n


def set_enforcing(session: Session, on: bool, by: Optional[str]) -> None:
    row = session.get(PrivacySetting, ENFORCE_KEY)
    old = row.value if row else "0"
    session.merge(PrivacySetting(key=ENFORCE_KEY, value="1" if on else "0", updated_by=by, updated_at=datetime.utcnow()))
    if old != ("1" if on else "0"):
        _log(session, domain=None, category=None, what="enforce", old=old, new="1" if on else "0", by=by)
    session.commit()


def retier(session: Session, domain: str, category: str, tier: Optional[str]) -> int:
    """Stamp every filed document and record of this type with the tier (NULL when unset)."""
    n = 0
    try:
        dom = Domain(domain)
    except ValueError:
        return 0
    for model in (Document, Record):
        for row in session.exec(select(model).where(model.domain == dom, model.category == category)):
            if row.sensitivity_tier != tier:
                row.sensitivity_tier = tier
                session.add(row)
                n += 1
    session.commit()
    return n


def recent_changes(session: Session, limit: int = 20) -> list[TierChange]:
    return list(session.exec(select(TierChange).order_by(TierChange.id.desc()).limit(limit)))
