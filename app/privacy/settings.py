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

from app.categories.registry import implemented_categories
from app.models.document import Document
from app.models.category import Category
from app.models.privacy import DocumentTypeSetting, PrivacySetting, TierChange
from app.models.record import Record
from app.privacy.tiers import Tier, TierBlocked, parse_tier

ENFORCE_KEY = "enforce_tiers"

# Starting suggestions shown pre-filled on the settings screen (Pedro, 2026-10-15), and the levels a category
# always has (decision D19), are declared on the kinds and categories themselves (work-list B2): KindSpec
# .suggested_tier, CategorySpec.forced_tier. A suggestion is NOT active until confirmed, and is never High.
FALLBACK_SUGGESTION = Tier.MID      # a kind that declares none: cautious middle until Pedro decides


def suggested_tiers() -> dict[tuple[str, str], Tier]:
    return {(spec.category.value, k.value): k.suggested_tier for spec in implemented_categories()
            for k in spec.kinds if k.suggested_tier is not None}


def forced_tiers() -> dict[str, Tier]:
    return {spec.category.value: spec.forced_tier for spec in implemented_categories() if spec.forced_tier is not None}


def forced_tier(category: object) -> Optional[Tier]:
    return None if category is None else forced_tiers().get(_s(category))


class TierHeld(Exception):
    """Enforcement is on and this document type has no tier yet: the document waits."""

    def __init__(self, category: object, paper_kind: object):
        self.category, self.paper_kind = _s(category), paper_kind
        super().__init__(f"no tier set for {self.category}/{paper_kind}")


@dataclass(frozen=True)
class TypeInfo:
    category: str
    category_label: str
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
    for spec in implemented_categories():
        for c in spec.kinds:
            suggested = spec.forced_tier or c.suggested_tier or FALLBACK_SUGGESTION
            out.append(TypeInfo(spec.category.value, spec.label, c.value, c.label, c.description, suggested))
    return out


def _setting(session: Session, category: object, paper_kind: str) -> Optional[DocumentTypeSetting]:
    return session.exec(select(DocumentTypeSetting).where(
        DocumentTypeSetting.category == _s(category), DocumentTypeSetting.paper_kind == paper_kind)).first()


def configured_tier(session: Session, category: object, paper_kind: Optional[str]) -> Optional[str]:
    """The tier saved for a type, whether or not levels are binding yet (None = not set)."""
    if category is None:
        return None
    forced = forced_tier(category)
    if forced is not None:
        return forced.value
    if paper_kind is None:
        return None
    row = _setting(session, category, paper_kind)
    return row.tier if row is not None else None


def enforcing(session: Session) -> bool:
    row = session.get(PrivacySetting, ENFORCE_KEY)
    return row is not None and row.value == "1"


def resolve(session: Session, category: object, paper_kind: Optional[str]) -> Resolution:
    forced = forced_tier(category)
    if forced is not None:
        return Resolution("ready", forced, False)
    if not enforcing(session):
        return Resolution("off")
    row = _setting(session, category, paper_kind) if category is not None and paper_kind is not None else None
    if row is None or row.tier is None:
        return Resolution("held")
    return Resolution("ready", parse_tier(row.tier), row.redact)


def require_ready(session: Session, category: object, paper_kind: Optional[str]) -> Resolution:
    """The gate before anything reads a document: raises TierHeld while the type has no tier."""
    res = resolve(session, category, paper_kind)
    if res.state == "held":
        raise TierHeld(category, paper_kind)
    return res


def type_rows(session: Session) -> list[TypeRow]:
    saved = {(r.category, r.paper_kind): r for r in session.exec(select(DocumentTypeSetting))}
    rows = []
    for info in known_types():
        forced = forced_tiers().get(info.category)
        if forced is not None:
            rows.append(TypeRow(info, forced, False, forced, True, locked=True))
            continue
        row = saved.get((info.category, info.paper_kind))
        tier = parse_tier(row.tier) if row is not None and row.tier else None
        rows.append(TypeRow(info, tier, bool(row and row.redact), tier or info.suggested, row is not None and tier is not None))
    return rows


def _log(session, *, category, paper_kind, what, old, new, by) -> None:
    session.add(TierChange(category=category, paper_kind=paper_kind, what=what, old_value=old, new_value=new,
                           changed_by=by, changed_at=datetime.utcnow()))


def save_type(session: Session, category: str, paper_kind: str, tier: object, redact: bool, by: Optional[str]) -> None:
    """Set a type's tier (a tier value, or "" / None to unset) and redaction switch. Logged;
    documents and records already filed under the type are re-tiered at once."""
    if (category, paper_kind) not in {(i.category, i.paper_kind) for i in known_types()}:
        raise TierBlocked(f"unknown document type {category}/{paper_kind}")
    if forced_tier(category) is not None:
        raise TierBlocked("Health is always High and cannot be given another level.")
    new_tier = parse_tier(tier).value if tier not in (None, "") else None
    row = _setting(session, category, paper_kind)
    old_tier, old_redact = (row.tier, row.redact) if row else (None, False)
    if row is None:
        row = DocumentTypeSetting(category=category, paper_kind=paper_kind)
    row.tier, row.redact, row.updated_by, row.updated_at = new_tier, bool(redact), by, datetime.utcnow()
    session.add(row)
    if old_tier != new_tier:
        _log(session, category=category, paper_kind=paper_kind, what="tier", old=old_tier, new=new_tier, by=by)
    if old_redact != bool(redact):
        _log(session, category=category, paper_kind=paper_kind, what="redact", old=str(old_redact), new=str(bool(redact)), by=by)
    session.commit()
    retier(session, category, paper_kind, new_tier)


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
        _log(session, category=None, paper_kind=None, what="enforce", old=old, new="1" if on else "0", by=by)
    session.commit()


def retier(session: Session, category: str, paper_kind: str, tier: Optional[str]) -> int:
    """Stamp every filed document and record of this type with the tier (NULL when unset)."""
    n = 0
    try:
        dom = Category(category)
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
