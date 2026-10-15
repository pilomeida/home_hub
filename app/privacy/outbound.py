"""The outbound checkpoint: every call to the gateway passes through
`prepare` first (app.llm_gateway.GatewayClient.run). Fails closed — any
doubt raises TierBlocked and nothing is sent.

- sensitivity None   -> not wired yet (D2): passes through untouched, as today.
- house_low          -> passes; the gateway is told, and changes nothing.
- house_mid          -> the gateway is told; it routes only to zero-data-retention
                        endpoints. Redaction only if the caller asked for it (D3).
- house_high         -> blocked: no route exists yet.
- unknown value      -> blocked.
Redaction cannot see inside images/PDFs, so a call that asks for redaction
and carries attachments is blocked rather than half-redacted. Redaction with
tool use is not supported yet (placeholders in tool arguments would need
restoring) and is blocked too.

The audit record has tiers, counts and sizes only — never content."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from app.privacy.redactor import RedactionReport, Redactor
from app.privacy.tiers import Tier, TierBlocked, parse_tier, policy_for
from app.privacy.vault import Vault

logger = logging.getLogger("home_hub.privacy")


@dataclass
class Redaction:
    """What a caller hands over to ask for redaction on one call."""
    redactor: Redactor
    vault: Vault = field(default_factory=Vault)


@dataclass
class Outbound:
    system: str
    user: Optional[str]
    messages: Optional[list]
    sensitivity: Optional[str]           # value sent to the gateway (None = field omitted)
    vault: Optional[Vault]               # set only when text was redacted; used to restore the reply
    audit: dict


def _redact_messages(messages: list, redaction: Redaction, reports: list) -> list:
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            r = redaction.redactor.redact_or_block(content, redaction.vault)
            reports.append(r.report)
            out.append({**m, "content": r.text})
        elif isinstance(content, list):
            blocks = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    r = redaction.redactor.redact_or_block(b.get("text", ""), redaction.vault)
                    reports.append(r.report)
                    blocks.append({**b, "text": r.text})
                elif isinstance(b, dict) and b.get("type") in ("image", "document"):
                    raise TierBlocked("redaction requested but the call carries an image/document")
                else:
                    blocks.append(b)
            out.append({**m, "content": blocks})
        else:
            out.append(m)
    return out


def prepare(
    *, system: str, user: Optional[str], messages: Optional[list], tools: Optional[list],
    attachments: Optional[list], sensitivity: object = None, redaction: Optional[Redaction] = None,
) -> Outbound:
    audit: dict = {"tier": None, "redacted": False, "chars": len(system) + len(user or ""),
                   "attachments": len(attachments or []), "replaced": 0, "outcome": "sent"}
    try:
        if sensitivity is None:
            if redaction is not None:
                raise TierBlocked("redaction requested without a tier")
            audit["tier"] = "unwired"
            return Outbound(system, user, messages, None, None, audit)

        tier: Tier = parse_tier(sensitivity)
        audit["tier"] = tier.value
        policy = policy_for(tier)            # raises for house_high / unknown
        want_redaction = redaction is not None
        if policy.redact and not want_redaction:
            raise TierBlocked(f"{tier.value} requires redaction and none was supplied")
        if not want_redaction:
            return Outbound(system, user, messages, tier.value, None, audit)

        if attachments:
            raise TierBlocked("redaction requested but the call carries attachments (images/PDFs cannot be redacted)")
        if tools:
            raise TierBlocked("redaction with tool use is not supported yet")
        reports: list[RedactionReport] = []
        red_system = redaction.redactor.redact_or_block(system, redaction.vault)
        reports.append(red_system.report)
        red_user, red_messages = user, messages
        if user is not None:
            r = redaction.redactor.redact_or_block(user, redaction.vault)
            reports.append(r.report)
            red_user = r.text
        if messages is not None:
            red_messages = _redact_messages(messages, redaction, reports)
        audit["redacted"] = True
        audit["replaced"] = sum(r.replaced for r in reports)
        audit["by_kind"] = _merge([r.by_kind for r in reports])
        return Outbound(red_system.text, red_user, red_messages, tier.value, redaction.vault, audit)
    except TierBlocked as exc:
        audit["outcome"] = "blocked"
        audit["reason"] = exc.reason
        raise
    finally:
        # Counts and sizes only. Never the text, never a matched value.
        logger.info("outbound %s", {k: v for k, v in audit.items()})


def _merge(dicts: list[dict]) -> dict:
    out: dict = {}
    for d in dicts:
        for k, v in d.items():
            out[k] = out.get(k, 0) + v
    return out
