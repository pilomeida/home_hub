"""The one reader of past answers (PLAN step 2b): every classifier asks it first.

`find(session, purpose, facts)` returns the rule that answers, or None:
- only switched-on rules whose answer serves the purpose (`spending`: a spending leaf; `merchant`: a merchant name;
  `paper_kind`: a category and kind);
- matching one of the facts given (what it looks at -> the text to test);
- in one order: a person's rule, then the AI's, then the system's; then the higher priority; then the newest.
A person's answer is never overridden by an AI or system rule, whatever their priority (tested)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, Mapping, Optional

from sqlmodel import Session, select

from app.models.rule import (
    AI, CONTAINS, EXACT, KEY, PERSON, PREFIX, REGEX, SYSTEM, Rule,
)

SPENDING, MERCHANT, PAPER_KIND = "spending", "merchant", "paper_kind"
_SOURCE_ORDER = {PERSON: 0, AI: 1, SYSTEM: 2}


def _serves(rule: Rule, purpose: str) -> bool:
    if purpose == SPENDING:
        return rule.node_id is not None
    if purpose == MERCHANT:
        return bool(rule.merchant)
    if purpose == PAPER_KIND:
        return bool(rule.category and rule.paper_kind)
    raise ValueError(f"unknown rule purpose {purpose!r}")


def matches(rule: Rule, text: Optional[str]) -> bool:
    if not text or not rule.pattern:
        return False
    if rule.how == KEY:
        from app.services.provider_rules import provider_key
        return provider_key(text) == rule.pattern
    needle, hay = rule.pattern.strip().lower(), text.lower()
    if rule.how == CONTAINS:
        return needle in hay
    if rule.how == EXACT:
        return hay.strip() == needle
    if rule.how == PREFIX:
        return hay.strip().startswith(needle)
    if rule.how == REGEX:
        try:
            return re.search(rule.pattern, text, re.IGNORECASE) is not None
        except re.error:
            return False
    return False


def ordered(rules: Iterable[Rule]) -> list[Rule]:
    return sorted(rules, key=lambda r: (_SOURCE_ORDER.get(r.source, 9), -(r.priority or 0), -(r.id or 0)))


def candidates(session: Session, purpose: str, facts: Mapping[str, Optional[str]]) -> list[Rule]:
    """Every switched-on rule that serves the purpose and matches a fact, in the reader's order."""
    looks = [k for k, v in facts.items() if v]
    if not looks:
        return []
    rows = session.exec(select(Rule).where(Rule.enabled == True, Rule.looks_at.in_(looks))).all()  # noqa: E712
    return ordered(r for r in rows if _serves(r, purpose) and matches(r, facts.get(r.looks_at)))


def find(session: Session, purpose: str, facts: Mapping[str, Optional[str]], *, count_hit: bool = False) -> Optional[Rule]:
    hits = candidates(session, purpose, facts)
    if not hits:
        return None
    if count_hit:
        hits[0].hits += 1
        hits[0].last_hit_at = datetime.utcnow()
        session.add(hits[0])
    return hits[0]


def remember(session: Session, *, looks_at: str, how: str, pattern: str, source: str = PERSON,
             by: Optional[str] = None, note: str = "", example: Optional[str] = None, confidence: Optional[float] = None,
             enabled: bool = True, **answer) -> Rule:
    """Write (or update) the rule for this exact match and source. A person's answer replaces the earlier answer
    for the same match; it never creates a second rule for it. Does not commit."""
    pattern = pattern.strip() if how == REGEX else pattern.strip().lower()
    rule = session.exec(select(Rule).where(Rule.looks_at == looks_at, Rule.how == how, Rule.pattern == pattern,
                                           Rule.source == source)).first()
    if rule is None:
        rule = Rule(looks_at=looks_at, how=how, pattern=pattern, source=source)
    for key, value in answer.items():
        setattr(rule, key, value)
    rule.enabled, rule.confidence = enabled, confidence
    rule.note = note or rule.note
    rule.example = example or rule.example
    if source == PERSON or enabled:
        rule.approved_by, rule.approved_at = by or rule.approved_by, datetime.utcnow()
    session.add(rule)
    return rule


__all__ = ["SPENDING", "MERCHANT", "PAPER_KIND", "PERSON", "AI", "SYSTEM", "CONTAINS", "EXACT", "KEY", "PREFIX",
           "REGEX", "find", "candidates", "matches", "ordered", "remember"]
