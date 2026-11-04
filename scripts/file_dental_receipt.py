"""One-off (Pedro, 2026-10-30): the dental receipt of the Faculdade de Medicina Dentária (document 163, filed under
Health before bills left Health) becomes a bill filed under Bills > Health > Care > Dental, and that clinic's later
papers land there too. Run on the VPS, from the app folder, as the app's user, after the migration that adds the leaf:
    python scripts/file_dental_receipt.py [DOCUMENT_ID]
The Health entry is taken back by the re-file (the paper is not a Health paper any more)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine  # noqa: E402
from app.categories.financials.kinds import FinancialsKind  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.category import Category  # noqa: E402
from app.models.merchant import Merchant  # noqa: E402
from app.models.transaction import Transaction  # noqa: E402
from app.services.classification_engine import normalize_provider  # noqa: E402
from app.services.ingestion import Classification, refile_document  # noqa: E402
from app.services.taxonomy import file_transaction, get_node, path_label  # noqa: E402

DENTAL_SLUG = "health.care.dental"
CLINIC = "FACULDADE DE MEDICINA DENTÁRIA DA U.L."


def pin_clinic_to_dental(session: Session) -> Merchant:
    """The clinic's bills default to Dental, so this paper and the next ones do not depend on the model's guess."""
    node = get_node(session, DENTAL_SLUG)
    key = normalize_provider(CLINIC)
    merchant = session.exec(select(Merchant).where(Merchant.normalized_key == key)).first()
    if merchant is None:
        merchant = Merchant(canonical_name="Faculdade de Medicina Dentária da U.L.", normalized_key=key)
    merchant.default_category_id = node.id
    merchant.confirmed = True
    session.add(merchant)
    session.commit()
    return merchant


async def main(document_id: int) -> int:
    with Session(engine) as session:
        pin_clinic_to_dental(session)
        document = session.get(Document, document_id)
        if document is None:
            print(f"{document_id}: no such document")
            return 1
        if document.domain != Category.FINANCIALS:
            document = await refile_document(
                session, document, Classification(domain=Category.FINANCIALS, paper_kind=FinancialsKind.BILL.value))
        node = get_node(session, DENTAL_SLUG)
        transactions = session.exec(select(Transaction).where(Transaction.document_id == document_id)).all()
        for txn in transactions:
            if txn.category_id != node.id:
                file_transaction(session, txn, node)
        session.commit()
        for txn in transactions:
            print(f"{document_id}: {document.status.value}, {txn.description}, {txn.amount} -> {path_label(session, node)}")
        if not transactions:
            print(f"{document_id}: {document.status.value} {document.failure_reason or ''} (no bill line was read)".rstrip())
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 163)))
