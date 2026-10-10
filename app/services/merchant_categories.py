"""Apply a human's category instruction for a merchant, per direction.

'Category is income>psi' for a client: every money-in entry goes to income.psi.sessions and that becomes the merchant's
credit default; 'taxes' for a payee: every money-out entry goes there and it becomes the debit default. The human's
choice is honoured where a mechanical rule would hesitate (an entry typed 'transfer', a reversal credit under a loan);
entries tied to a loan are never moved."""

from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, select

from app.models.merchant import Merchant
from app.models.transaction import Transaction, TransactionType
from app.services.provider_rules import provider_fits
from app.services.taxonomy import file_transaction, get_node, legacy_category_for


@dataclass
class MerchantCategoryReport:
    filed: int = 0
    skipped: int = 0


def apply_merchant_category(session: Session, merchant_id: int, credit_slug: Optional[str] = None,
                            debit_slug: Optional[str] = None, dry_run: bool = False) -> MerchantCategoryReport:
    report = MerchantCategoryReport()
    merchant = session.get(Merchant, merchant_id)
    if merchant is None:
        return report
    credit = get_node(session, credit_slug) if credit_slug else None
    debit = get_node(session, debit_slug) if debit_slug else None
    if not dry_run:
        if credit is not None and credit.kind == "in":
            merchant.default_credit_category_id = credit.id
        if debit is not None and debit.kind != "in":
            merchant.default_category_id = debit.id
            merchant.default_category = legacy_category_for(session, debit)
        session.add(merchant)
    for t in session.exec(select(Transaction).where(Transaction.merchant_id == merchant_id)).all():
        if t.debt_id is not None or t.settled_by_id is not None:
            continue
        node = credit if t.transaction_type == TransactionType.CREDIT else debit
        if node is None or t.category_id == node.id:
            continue
        if not provider_fits(session, t, node):
            report.skipped += 1
            continue
        report.filed += 1
        if not dry_run:
            file_transaction(session, t, node)
    if not dry_run:
        session.commit()
    return report
