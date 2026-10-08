"""Put the loan instalments and loan-insurance debits on their one fixed merchant each."""

from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.models.merchant import Merchant
from app.models.transaction import Nature, Transaction
from app.services.structured_providers import StructuredMerchant, structured_merchant
from app.services.taxonomy import get_node, legacy_category_for


@dataclass
class RelabelReport:
    transactions_relabelled: int = 0
    merchants_created: int = 0
    merchants_left_empty: int = 0
    moves: list[tuple[int, int | None, int]] = field(default_factory=list)  # (txn, old merchant, new merchant)


def ensure_structured_merchant(session: Session, spec: StructuredMerchant) -> tuple[Merchant, bool]:
    merchant = session.exec(select(Merchant).where(Merchant.normalized_key == spec.key)).first()
    if merchant is not None:
        return merchant, False
    node = get_node(session, spec.node_slug)
    merchant = Merchant(canonical_name=spec.name, normalized_key=spec.key, default_category_id=node.id,
                        default_category=legacy_category_for(session, node),
                        default_nature=Nature.ESSENTIAL, confirmed=True)
    session.add(merchant)
    session.flush()
    return merchant, True


def relabel_structured(session: Session, dry_run: bool = True) -> RelabelReport:
    """Only the merchant link changes: never a category, never a debt link."""
    report = RelabelReport()
    made: dict[str, Merchant] = {}
    old_ids: set[int] = set()
    for t in session.exec(select(Transaction)).all():
        spec = structured_merchant(t.provider)
        if spec is None:
            continue
        if spec.key not in made:
            if dry_run:
                existing = session.exec(select(Merchant).where(Merchant.normalized_key == spec.key)).first()
                made[spec.key] = existing or Merchant(id=-1, canonical_name=spec.name, normalized_key=spec.key)
                report.merchants_created += 0 if existing else 1
            else:
                made[spec.key], created = ensure_structured_merchant(session, spec)
                report.merchants_created += int(created)
        target = made[spec.key]
        if t.merchant_id == target.id:
            continue
        report.moves.append((t.id, t.merchant_id, target.id))
        report.transactions_relabelled += 1
        if t.merchant_id:
            old_ids.add(t.merchant_id)
        if not dry_run:
            t.merchant_id = target.id
            session.add(t)
    if not dry_run:
        session.flush()
        for mid in old_ids:
            if session.exec(select(Transaction.id).where(Transaction.merchant_id == mid).limit(1)).first() is None:
                report.merchants_left_empty += 1
        session.commit()
    return report
