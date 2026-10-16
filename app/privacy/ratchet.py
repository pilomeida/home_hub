"""The one-way privacy ratchet (decision D18, docs/privacy/DECISIONS.md).

A run (an Ask question, the weekly wiki check, an intake read) has a level. When it touches content
whose type is High it is raised to High, and from that moment every call it makes goes out at High
only, until the run ends. It never goes down, and nothing an explicit `sensitivity=` says can lower it.
Content sent at a lower level BEFORE the rise was allowed at that level; nothing after goes out below.

The gateway client calls `apply_ratchet` on every call (app/llm_gateway.py), so no call site has to
remember. Each run enters its own `ratchet_scope` (contextvars keep concurrent runs apart)."""

from __future__ import annotations

import contextvars
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterable, Optional

from app.privacy.tiers import Tier, parse_tier

_RANK = {None: 0, Tier.LOW: 1, Tier.MID: 2, Tier.HIGH: 3}


def rank(tier: Optional[Tier]) -> int:
    return _RANK[tier]


def highest(tiers: Iterable[Optional[Tier]]) -> Optional[Tier]:
    best: Optional[Tier] = None
    for tier in tiers:
        if rank(tier) > rank(best):
            best = tier
    return best


@dataclass(frozen=True)
class Escalation:
    step: int                    # which model round-trip had just run (1 = the first)
    from_tier: Optional[str]
    to_tier: str
    label: str                   # plain words: what was opened ("3 Health records")


@dataclass
class Ratchet:
    tier: Optional[Tier] = None
    started_private: bool = False
    escalations: list[Escalation] = field(default_factory=list)

    def raise_to(self, tier: Optional[Tier], *, step: int, label: str) -> bool:
        if rank(tier) <= rank(self.tier):
            return False
        self.escalations.append(Escalation(step, self.tier.value if self.tier else None, tier.value, label))
        self.tier = tier
        return True

    @property
    def private(self) -> bool:
        return self.tier == Tier.HIGH

    def to_json(self) -> str:
        return json.dumps({"started_private": self.started_private,
                           "escalations": [vars(e) for e in self.escalations]})


@dataclass(frozen=True)
class RatchetLog:
    """A stored ratchet (AskTurn.escalation_json), read back."""
    started_private: bool = False
    escalations: tuple[Escalation, ...] = ()

    @property
    def private(self) -> bool:
        return self.started_private or any(e.to_tier == Tier.HIGH.value for e in self.escalations)

    def first_private(self) -> Optional[Escalation]:
        return next((e for e in self.escalations if e.to_tier == Tier.HIGH.value), None)

    @staticmethod
    def from_json(raw: Optional[str]) -> "RatchetLog":
        """Fails CLOSED: an empty value is an unused log (not private); a non-empty value that does not
        parse into the expected shape is read as private, so a damaged record never opens a lower level."""
        if not raw:
            return RatchetLog()
        try:
            return _parse_log(json.loads(raw))
        except (ValueError, TypeError, AttributeError, KeyError):
            return RatchetLog(started_private=True)


def _parse_log(data: object) -> RatchetLog:
    """The shape to_json writes, checked field by field. Anything else raises ValueError."""
    if not isinstance(data, dict):
        raise ValueError("a ratchet log is a JSON object")
    started = data["started_private"]
    if not isinstance(started, bool):
        raise ValueError("started_private is not a bool")
    raw_escalations = data["escalations"]
    if not isinstance(raw_escalations, list):
        raise ValueError("escalations is not a list")
    escalations = []
    for e in raw_escalations:
        if not isinstance(e, dict):
            raise ValueError("an escalation is not an object")
        step, from_tier, to_tier, label = e["step"], e["from_tier"], e["to_tier"], e["label"]
        if not isinstance(step, int) or isinstance(step, bool):
            raise ValueError("step is not an int")
        if from_tier is not None and not isinstance(from_tier, str):
            raise ValueError("from_tier is not a string")
        if not isinstance(to_tier, str) or not isinstance(label, str):
            raise ValueError("to_tier or label is not a string")
        escalations.append(Escalation(step, from_tier, to_tier, label))
    return RatchetLog(started, tuple(escalations))


_current: contextvars.ContextVar = contextvars.ContextVar("hub_privacy_ratchet", default=None)


def current_ratchet() -> Optional[Ratchet]:
    return _current.get()


@contextmanager
def ratchet_scope(start: Optional[Tier] = None):
    ratchet = Ratchet(tier=start, started_private=(start == Tier.HIGH))
    token = _current.set(ratchet)
    try:
        yield ratchet
    finally:
        _current.reset(token)


def apply_ratchet(sensitivity: object, redaction):
    """The level and redaction a call may use inside the current run: never below the ratchet, and no
    redaction at High (the route is end-to-end encrypted; redaction would add nothing, app/privacy/tiers.py)."""
    ratchet = _current.get()
    if ratchet is None or ratchet.tier is None:
        return sensitivity, redaction
    current = parse_tier(sensitivity) if sensitivity is not None else None   # garbled -> TierBlocked (fail closed)
    if rank(ratchet.tier) > rank(current):
        sensitivity = ratchet.tier.value
    if sensitivity == Tier.HIGH.value:
        redaction = None
    return sensitivity, redaction
