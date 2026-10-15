"""Sensitivity tiers (decision D1, docs/privacy/DECISIONS.md).

house_low  any provider:model
house_mid  zero-data-retention endpoints only
house_high highly sensitive; route TBD (likely dedicated inference capacity)

Separate from the gateway's own quality tiers (low/high/top) and privacy
tiers (personal/private/business/public), which this module never touches.

Which tier a document type has is configuration kept in the database and
edited on the Hub (app/privacy/settings.py, decision D12) — never in code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Tier(str, Enum):
    LOW = "house_low"
    MID = "house_mid"
    HIGH = "house_high"


class TierBlocked(Exception):
    """A call may not be made. Fail closed: never caught and downgraded."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class RoutePolicy:
    tier: Tier
    requires_zdr: bool        # only zero-data-retention endpoints
    route_available: bool     # False: nothing may be sent (house_high today)
    redact: bool              # deterministic redaction before leaving the VPS


# Redaction is a per-tier default here and may be overridden per document
# type when wiring (D3). MID: available but off until chosen per type.
POLICIES: dict[Tier, RoutePolicy] = {
    Tier.LOW: RoutePolicy(Tier.LOW, requires_zdr=False, route_available=True, redact=False),
    Tier.MID: RoutePolicy(Tier.MID, requires_zdr=True, route_available=True, redact=False),
    Tier.HIGH: RoutePolicy(Tier.HIGH, requires_zdr=True, route_available=False, redact=True),
}

def parse_tier(value: object) -> Tier:
    """Unknown or malformed -> TierBlocked (fail closed), never a default."""
    if isinstance(value, Tier):
        return value
    try:
        return Tier(value)
    except ValueError:
        raise TierBlocked(f"unknown sensitivity tier {value!r}") from None


def policy_for(tier: object) -> RoutePolicy:
    """The policy a call must obey. Raises TierBlocked if the tier is unknown
    or has no route (house_high until Pedro's dedicated route exists)."""
    policy = POLICIES[parse_tier(tier)]
    if not policy.route_available:
        raise TierBlocked(f"{policy.tier.value}: no route is available yet")
    return policy
