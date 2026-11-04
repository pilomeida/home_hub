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

from app.categories.registry import implemented_domains
from app.models.document import Document
from app.models.category import Category
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
    ("insurance", "policy"): Tier.MID,
    ("insurance", "claim"): Tier.MID,
}
FALLBACK_SUGGESTION = Tier.MID      # a type added later: cautious middle until Pedro decides

# Domains whose types are ALWAYS at this level, whether or not levels are binding and whatever the
# settings table says (decision D19). Health is the only one: nothing about it may ever be read below High.
FORCED_TIERS: dict[str, Tier] = {"health": Tier.HIGH}


def forced_tier(domain: object) -> Optional[Tier]:
    return None if domain is None else FORCED_TIERS.get(_s(domain))


class TierHeld(Exception):
    """Enforcement is on and this document type has no tier yet: the document waits."""

    def __init__(self, domain: object, paper_kind: object):
        self.category, self.paper_kind = _s(domain), paper_kind
        super().__init__(f"no tier set for {self.category}/{paper_kind}")


@dataclass(frozen=True)
class TypeInfo:
    category: str
    domain_label: str
    paper_kind: str
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
    locked: bool = False              # forced level (Health): shown, not editable


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
        for c in spec.kinds:
            key = (spec.category.value, c.value)
            suggested = FORCED_TIERS.get(spec.category.value) or SUGGESTED_TIERS.get(key, FALLBACK_SUGGESTION)
            out.append(TypeInfo(spec.category.value, spec.label, c.value, c.label, c.description, suggested))
    return out


def _setting(session: Session, domain: object, paper_kind: str) -> Optional[DocumentTypeSetting]:
    return session.exec(select(DocumentTypeSetting).where(
        DocumentTypeSetting.category == _s(domain), DocumentTypeSetting.paper_kind == paper_kind)).first()


def configured_tier(session: Session, domain: object, paper_kind: Optional[str]) -> Optional[str]:
    """The tier saved for a type, whether or not levels are binding yet (None = not set)."""
    if domain is None:
        return None
    forced = forced_tier(domain)
    if forced is not None:
        return forced.value
    if paper_kind is None:
        return None
    row = _setting(session, domain, paper_kind)
    return row.tier if row is not None else None


def enforcing(session: Session) -> bool:
    row = session.get(PrivacySetting, ENFORCE_KEY)
    return row is not None and row.value == "1"


def resolve(session: Session, domain: object, paper_kind: Optional[str]) -> Resolution:
    forced = forced_tier(domain)
    if forced is not None:
        return Resolution("ready", forced, False)
    if not enforcing(session):
        return Resolution("off")
    row = _setting(session, domain, paper_kind) if domain is not None and paper_kind is not None else None
    if row is None or row.tier is None:
        return Resolution("held")
    return Resolution("ready", parse_tier(row.tier), row.redact)


def require_ready(session: Session, domain: object, paper_kind: Optional[str]) -> Resolution:
    """The gate before anything reads a document: raises TierHeld while the type has no tier."""
    res = resolve(session, domain, paper_kind)
    if res.state == "held":
        raise TierHeld(domain, paper_kind)
    return res


def type_rows(session: Session) -> list[TypeRow]:
    saved = {(r.category, r.paper_kind): r for r in session.exec(select(DocumentTypeSetting))}
    rows = []
    for info in known_types():
        forced = FORCED_TIERS.get(info.category)
        if forced is not None:
            rows.append(TypeRow(info, forced, False, forced, True, locked=True))
            continue
        row = saved.get((info.category, info.paper_kind))
        tier = parse_tier(row.tier) if row is not None and row.tier else None
        rows.append(TypeRow(info, tier, bool(row and row.redact), tier or info.suggested, row is not None and tier is not None))
    return rows


def _log(session, *, domain, paper_kind, what, old, new, by) -> None:
    session.add(TierChange(category=domain, paper_kind=paper_kind, what=what, old_value=old, new_value=new,
                           changed_by=by, changed_at=datetime.utcnow()))


def save_type(session: Session, domain: str, paper_kind: str, tier: object, redact: bool, by: Optional[str]) -> None:
    """Set a type's tier (a tier value, or "" / None to unset) and redaction switch. Logged;
    documents and records already filed under the type are re-tiered at once."""
    if (domain, paper_kind) not in {(i.category, i.paper_kind) for i in known_types()}:
        raise TierBlocked(f"unknown document type {domain}/{paper_kind}")
    if forced_tier(domain) is not None:
        raise TierBlocked("Health is always High and cannot be given another level.")
    new_tier = parse_tier(tier).value if tier not in (None, "") else None
    row = _setting(session, domain, paper_kind)
    old_tier, old_redact = (row.tier, row.redact) if row else (None, False)
    if row is None:
        row = DocumentTypeSetting(category=domain, paper_kind=paper_kind)
    row.tier, row.redact, row.updated_by, row.updated_at = new_tier, bool(redact), by, datetime.utcnow()
    session.add(row)
    if old_tier != new_tier:
        _log(session, domain=domain, paper_kind=paper_kind, what="tier", old=old_tier, new=new_tier, by=by)
    if old_redact != bool(redact):
        _log(session, domain=domain, paper_kind=paper_kind, what="redact", old=str(old_redact), new=str(bool(redact)), by=by)
    session.commit()
    retier(session, domain, paper_kind, new_tier)


def confirm_suggestions(session: Session, by: Optional[str]) -> int:
    """Save the suggested tier for every type that has none yet. Returns how many."""
    n = 0
    for r in type_rows(session):
        if r.tier is None:
            save_type(session, r.info.category, r.info.paper_kind, r.info.suggested, r.redact, by)
            n += 1
    return n


def set_enforcing(session: Session, on: bool, by: Optional[str]) -> None:
    row = session.get(PrivacySetting, ENFORCE_KEY)
    old = row.value if row else "0"
    session.merge(PrivacySetting(key=ENFORCE_KEY, value="1" if on else "0", updated_by=by, updated_at=datetime.utcnow()))
    if old != ("1" if on else "0"):
        _log(session, domain=None, paper_kind=None, what="enforce", old=old, new="1" if on else "0", by=by)
    session.commit()


def retier(session: Session, domain: str, paper_kind: str, tier: Optional[str]) -> int:
    """Stamp every filed document and record of this type with the tier (NULL when unset)."""
    n = 0
    try:
        dom = Category(domain)
    except ValueError:
        return 0
    for model in (Document, Record):
        for row in session.exec(select(model).where(model.category == dom, model.paper_kind == paper_kind)):
            if row.sensitivity_tier != tier:
                row.sensitivity_tier = tier
                session.add(row)
                n += 1
    session.commit()
    return n


def recent_changes(session: Session, limit: int = 20) -> list[TierChange]:
    return list(session.exec(select(TierChange).order_by(TierChange.id.desc()).limit(limit)))
