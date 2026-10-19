"""Multibanco payment entities (docs/payment-entities.md). A line `PAG SERVICOS 1835 27134-627088996` pays
entity 27134 with reference 627088996: the entity is who was paid, so it fixes the merchant, whatever the
payment institution (Eupago, Easypay) prints. Categories are Pedro's (2026-10-19)."""
import re
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, select

from app.models.transaction import Transaction
from app.services.taxonomy import file_transaction, get_node

_ENTITY = re.compile(r"(?<![\d-])(\d{5})-\d{8,9}\b")


@dataclass(frozen=True)
class PaymentEntity:
    number: str
    name: str
    node_slug: str


ENTITIES = {e.number: e for e in (
    PaymentEntity("27134", "Município de Mafra", "family.sports.sports-fees"),
    PaymentEntity("23332", "Câmara Municipal de Mafra", "taxes-financial-costs.taxes.other-taxes-fees"),
    PaymentEntity("40141", "LigaT Telecom", "housing.utilities.telecom"),
    PaymentEntity("12442", "Amigo (Vodafone)", "housing.utilities.telecom"),
    PaymentEntity("12605", "Fixando", "psi-expenses.practice-costs.platform-licences"),
    PaymentEntity("10095", "Estado, Taxas e Impostos", "taxes-financial-costs.taxes.other-taxes-fees"),
    PaymentEntity("21056", "Estado, Segurança Social", "taxes-financial-costs.taxes.social-security"),
    PaymentEntity("20160", "Santa Casa da Misericórdia", "family.leisure.betting"),
    PaymentEntity("22255", "LigaT Telecom", "housing.utilities.telecom"),
    PaymentEntity("21317", "Ordem dos Psicólogos", "psi-expenses.practice-costs.professional-membership"),
    # Tranches of Matias' snow trip, paid by us and reimbursed by him afterwards: money lent to family.
    PaymentEntity("12092", "Wamos", "loans-debt.money-lent-out.to-family"),
    PaymentEntity("10597", "Hospital CUF Descobertas", "health.care.medical-appointments"),
    PaymentEntity("11797", "Instituto dos Registos e do Notariado", "taxes-financial-costs.taxes.other-taxes-fees"),
    PaymentEntity("20998", "Autoestrada", "transport.car-running-costs.tolls-parking"),
)}

# Payments whose text names the payee but prints no entity number (Pedro, 2026-10-19). The key is the text rule's
# own, used like an entity number for the merchant key.
_TEXT_RULES = (
    (re.compile(r"PAG SERVICOS\s+BGA\b", re.I),
     PaymentEntity("text:bga", "Brave Generation Academy", "family.education.school-fees")),
    (re.compile(r"LIGAT", re.I), PaymentEntity("text:ligat", "LigaT Telecom", "housing.utilities.telecom")),
    (re.compile(r"AMIGO\*EASYPAY", re.I), PaymentEntity("text:amigo", "Amigo (Vodafone)", "housing.utilities.telecom")),
    # a card-3315 purchase at Eupago (the card prints Eupago, not the payee) is Fixando
    (re.compile(r"COMPRA\s+\*?3315\s+EUPAGO", re.I),
     PaymentEntity("text:fixando", "Fixando", "psi-expenses.practice-costs.platform-licences")),
)


def entity_of(description: Optional[str]) -> Optional[PaymentEntity]:
    m = _ENTITY.search(description or "")
    return ENTITIES.get(m.group(1)) if m else None


def payee_of(description: Optional[str]) -> Optional[PaymentEntity]:
    """The payee a payment line names: by entity number first, then by the text rules."""
    entity = entity_of(description)
    if entity is not None:
        return entity
    return next((e for pattern, e in _TEXT_RULES if pattern.search(description or "")), None)


def refile_by_entity(session: Session, dry_run: bool = True) -> list[tuple[Transaction, PaymentEntity]]:
    """Every stored entry that pays one of these entities: its merchant and its category follow the entity.
    An entry a person has checked (`checked_by`) is left alone."""
    from app.services.merchant_relabel import ensure_structured_merchant
    from app.services.structured_providers import entity_merchant
    changed = []
    for t in session.exec(select(Transaction)).all():     # keeps held rows: an entry's payee is not a spending figure
        entity = payee_of(t.description)
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
