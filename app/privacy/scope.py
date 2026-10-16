"""The privacy scope: while a document is being processed, every AI call made on
its behalf carries the document type's level (and redaction, if switched on)
without each call site having to remember (decision D12).

    with privacy_scope(session, domain, category):
        ...extraction, wiki ingest, anything that talks to the gateway...

- enforcement off          -> no-op: calls behave exactly as before.
- enforcement on, set      -> calls carry the type's tier (and a Redaction if on).
- enforcement on, not set  -> TierHeld: nothing is read.
- category unknown (a domain that works out its own category, e.g. Financials):
  the STRICTEST level among that domain's categories applies; if any is unset, held.
An explicit sensitivity= on a gateway call always wins over the scope."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, select

from app.config import settings as app_settings
from app.models.account import Account
from app.models.person import Person
from app.privacy import settings as ps
from app.privacy.outbound import Redaction
from app.privacy.reach import _current as _reach_var, compute_reach
from app.privacy.redactor import Redactor
from app.privacy.tiers import Tier
from app.privacy.vault import Vault

_ORDER = {Tier.LOW: 0, Tier.MID: 1, Tier.HIGH: 2}


@dataclass(frozen=True)
class Scope:
    tier: Tier
    redaction: Optional[Redaction]


_current: contextvars.ContextVar = contextvars.ContextVar("hub_privacy_scope", default=None)


def current_scope() -> Optional[Scope]:
    return _current.get()


def known_terms(session: Session) -> dict[str, list[str]]:
    """Values the Hub already knows and must hide when redaction is on."""
    names = [p.name for p in session.exec(select(Person)) if p.name]
    names += list(app_settings.telegram_allowed_users.values())
    accounts = [a.identifier for a in session.exec(select(Account)) if a.identifier]
    terms: dict[str, list[str]] = {"NAME": names, "ACCOUNT": accounts, "ADDRESS": [], "POLICY": []}
    # HUB_PRIVACY_TERMS="ADDRESS:Rua X 1, Lisboa;POLICY:AP-123" — extra values to hide.
    for part in (app_settings.PRIVACY_TERMS or "").split(";"):
        kind, _, value = part.partition(":")
        if kind.strip().upper() in terms and value.strip():
            terms[kind.strip().upper()].append(value.strip())
    return terms


def resolve_strictest(session: Session, domain: object, category: Optional[str]) -> ps.Resolution:
    forced = ps.forced_tier(domain)          # a forced domain (Health) is High on every path, binding or not
    if forced is not None:
        return ps.Resolution("ready", forced, False)
    if category is not None:
        return ps.resolve(session, domain, category)
    if not ps.enforcing(session):
        return ps.Resolution("off")
    cats = [i.category for i in ps.known_types() if i.domain == ps._s(domain)]
    resolved = [ps.resolve(session, domain, c) for c in cats]
    if not resolved or any(r.state != "ready" for r in resolved):
        return ps.Resolution("held")
    top = max(resolved, key=lambda r: _ORDER[r.tier])
    return ps.Resolution("ready", top.tier, any(r.redact for r in resolved))


def require_ready(session: Session, domain: object, category: Optional[str]) -> ps.Resolution:
    res = resolve_strictest(session, domain, category)
    if res.state == "held":
        raise ps.TierHeld(domain, category)
    return res


@contextmanager
def privacy_scope(session: Session, domain: object, category: Optional[str]):
    res = require_ready(session, domain, category)
    if res.state == "off":
        yield None
        return
    redaction = Redaction(Redactor(known_terms(session)), Vault()) if res.redact else None
    token = _current.set(Scope(res.tier, redaction))
    try:
        yield _current.get()
    finally:
        _current.reset(token)


@contextmanager
def reachable_scope(session: Session):
    """Ask and the weekly wiki check read documents of every type, so they run inside THIS scope
    (decision D16, app/privacy/reach.py): the call level is the strictest level among the types they
    may read, and everything they read is filtered to those types. Enter it inside the code that
    actually runs (a background task has its own session), not only in the request handler.

    - levels not binding -> a non-binding Reach and no Scope: calls behave exactly as before.
    - binding            -> Scope(tier=strictest included tier, no redaction: a type that hides
                            personal details is never included) and a Reach snapshot for the filters."""
    reach = compute_reach(session)
    scope_token = _current.set(Scope(reach.tier, None)) if reach.binding else None
    reach_token = _reach_var.set(reach)
    try:
        yield reach
    finally:
        _reach_var.reset(reach_token)
        if scope_token is not None:
            _current.reset(scope_token)
