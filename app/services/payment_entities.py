"""Who was paid, read from the bank text by Pedro's payee rules (PLAN step 9 D8; the first list is in
app/services/payee_seed.py). Two kinds of payee rule, both person rules in the rules table that look at the bank
text and name a merchant:
- a payee with a merchant key: a Multibanco entity (`PAG SERVICOS 1835 27134-627088996` pays entity 27134) or a
  payment text naming the payee. The payee fixes the merchant (one merchant per key) and its leaf, whatever the
  payment institution (Eupago, Easypay) prints;
- a supermarket chain (no key): a new line's counterparty is the chain and its leaf supermarket.
A rule switched off on the rules table stops answering; nothing here holds a list of its own."""
import re
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, select

from app.models.category_node import CategoryNode
from app.models.rule import DESCRIPTION, ENTITY, EXACT, PERSON, REGEX, Rule
from app.models.transaction import Transaction
from app.services import payee_seed
from app.services.rules import matches, ordered
from app.services.taxonomy import file_transaction, get_node

_ENTITY = re.compile(r"(?<![\d-])(\d{5})-\d{8,9}\b")
_KEY_PREFIX = "entity:"


@dataclass(frozen=True)
class PaymentEntity:
    number: str
    name: str
    node_slug: str


def entity_number(description: Optional[str]) -> Optional[str]:
    m = _ENTITY.search(description or "")
    return m.group(1) if m else None


def _payee_rules(session: Session) -> list[Rule]:
    rows = session.exec(select(Rule).where(
        Rule.enabled == True, Rule.looks_at.in_((ENTITY, DESCRIPTION)),  # noqa: E712
        Rule.merchant.is_not(None), Rule.node_id.is_not(None))).all()
    return ordered(rows)


def _slug(session: Session, rule: Rule) -> Optional[str]:
    node = session.get(CategoryNode, rule.node_id)
    return node.slug if node is not None else None


def payee_of(session: Session, description: Optional[str]) -> Optional[PaymentEntity]:
    """The payee a payment line names: by entity number first, then by a payment text."""
    keyed = [r for r in _payee_rules(session) if r.merchant_key]
    number = entity_number(description)
    hit = next((r for r in keyed if r.looks_at == ENTITY and matches(r, number)), None) if number else None
    hit = hit or next((r for r in keyed if r.looks_at == DESCRIPTION and matches(r, description)), None)
    slug = _slug(session, hit) if hit is not None else None
    if slug is None:
        return None
    return PaymentEntity(hit.merchant_key.removeprefix(_KEY_PREFIX), hit.merchant, slug)


def _where(rule: Rule, text: str) -> int:
    """Where in the text the rule matches (a text naming two chains is the one named first)."""
    if rule.how == REGEX:
        try:
            found = re.search(rule.pattern, text, re.IGNORECASE)
        except re.error:
            found = None
        return found.start() if found else len(text)
    at = text.lower().find(rule.pattern.lower())
    return at if at >= 0 else len(text)


def chain_of(session: Session, *texts: Optional[str]) -> Optional[tuple[str, str]]:
    """(leaf slug, chain name) of the chain a text names first, trying the texts in their order."""
    chains = [r for r in _payee_rules(session) if not r.merchant_key and r.looks_at == DESCRIPTION]
    for text in texts:
        hits = [r for r in chains if matches(r, text) and _slug(session, r)]
        if hits:
            rule = min(hits, key=lambda r: _where(r, text))       # min keeps the reader's order on a tie
            return _slug(session, rule), rule.merchant
    return None


def seed(session: Session, *, by: str = "Pedro") -> int:
    """Write Pedro's first payee rulings as his person rules (the migration does this once on live; tests call it
    for a database built empty). The tree must exist. Does not commit."""
    from app.services import rules
    note = "Pedro's ruling, moved from the code into the rules (PLAN step 9 D8)"
    made = 0
    for number, name, slug in payee_seed.ENTITIES:
        rules.remember(session, looks_at=ENTITY, how=EXACT, pattern=number, source=PERSON, by=by, note=note,
                       merchant=name, node_id=get_node(session, slug).id, merchant_key=_KEY_PREFIX + number,
                       example=f"PAG SERVICOS {number}-000000000")
        made += 1
    for pattern, key, name, slug in payee_seed.TEXTS:
        rules.remember(session, looks_at=DESCRIPTION, how=REGEX, pattern=pattern, source=PERSON, by=by, note=note,
                       merchant=name, node_id=get_node(session, slug).id, merchant_key=_KEY_PREFIX + key)
        made += 1
    leaf = get_node(session, payee_seed.CHAIN_LEAF).id
    for pattern, name in payee_seed.CHAINS:
        rules.remember(session, looks_at=DESCRIPTION, how=REGEX, pattern=pattern, source=PERSON, by=by, note=note,
                       merchant=name, node_id=leaf)
        made += 1
    return made


def refile_by_entity(session: Session, dry_run: bool = True) -> list[tuple[Transaction, PaymentEntity]]:
    """Every stored entry that pays a known payee: its merchant and its category follow the payee.
    An entry a person has checked (`checked_by`) is left alone."""
    from app.services.merchant_relabel import ensure_structured_merchant
    from app.services.structured_providers import entity_merchant
    changed = []
    for t in session.exec(select(Transaction)).all():     # keeps held rows: an entry's payee is not a spending figure
        entity = payee_of(session, t.description)
        if entity is None or t.checked_by:
            continue
        node = get_node(session, entity.node_slug)
        if t.category_id == node.id and t.merchant_id is not None:
            continue
        changed.append((t, entity))
        if not dry_run:
            merchant, _ = ensure_structured_merchant(session, entity_merchant(entity))
            t.merchant_id = merchant.id
            file_transaction(session, t, node)
    if not dry_run:
        session.commit()
    return changed
