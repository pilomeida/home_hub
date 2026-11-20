"""Merchant rules that are not about one payee. The supermarket chains were here; since PLAN step 9 D8 they are
Pedro's person rules in the rules table, read through app/services/payment_entities.py."""

from typing import Optional

from sqlmodel import Session


def keyword_match(session: Session, *texts: Optional[str]) -> Optional[tuple[str, str]]:
    """(leaf slug, chain name) of the first supermarket-chain rule matching any of the texts, or None."""
    from app.services.payment_entities import chain_of
    return chain_of(session, *texts)


def keyword_node_slug(session: Session, *texts: Optional[str]) -> Optional[str]:
    """Slug of the first chain rule matching any of the texts (merchant name, bank provider string)."""
    match = keyword_match(session, *texts)
    return match[0] if match else None


# Counterparties that sell many kinds of things: a bank's mortgage, savings fund, card payment and fees are
# all "Santander". Their entries are classified by the description, never by one default category.
MULTI_PURPOSE = {"santander", "revolut", "millennium bcp", "bcp", "novo banco", "caixa geral de depositos", "paypal", "aegon santander"}


def is_multi_purpose(name: Optional[str]) -> bool:
    from app.services.merchant_merge import name_key
    return name_key(name) in MULTI_PURPOSE
