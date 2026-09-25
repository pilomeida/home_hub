import pytest
from sqlmodel import select

from app.domains import registry
from app.models.commitment import Cadence, Commitment
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.domain import Domain
from app.models.todo import Todo
from app.models.transaction import Category, Transaction, TransactionType
from app.models.utility_reading import UtilityReading, UtilityType
from app.services.ingestion import Classification, RefileRefusedError, refile_document


def _bill(session):
    document = Document(filename="boiler-invoice.pdf", file_path="/tmp/b.pdf", content_hash="rf1",
                        source=DocumentSource.MANUAL, status=DocumentStatus.PROCESSED,
                        domain=Domain.FINANCIALS, category="bill", account_id=None)
    session.add(document)
    session.commit()
    session.refresh(document)
    transaction = Transaction(document_id=document.id, provider="Worten", category=Category.OTHER_EXPENSE,
                              transaction_type=TransactionType.DEBIT, amount=499.0)
    session.add(transaction)
    session.commit()
    session.refresh(transaction)
    session.add(Todo(title="Pay Worten", transaction_id=transaction.id, domain=Domain.FINANCIALS))
    session.add(Todo(title="Paid earlier", transaction_id=transaction.id, domain=Domain.FINANCIALS, done=True))
    session.add(UtilityReading(document_id=document.id, utility_type=UtilityType.ELECTRICITY, period_label="2026-08",
                               cost_total=45.0))
    session.commit()
    return document, transaction


@pytest.mark.asyncio
async def test_refiling_a_bill_into_house_reverses_financials_derivations(session, monkeypatch):
    import app.domains.house.handler as house_handler
    from app.domains.house.warranty import WarrantyDates

    async def no_dates(file_path, client=None):
        return WarrantyDates(None, None)

    monkeypatch.setattr(house_handler, "extract_warranty_dates", no_dates)
    document, _ = _bill(session)

    result = await refile_document(session, document, Classification(
        Domain.HOUSE, "warranty_invoice", {"item_name": "Boiler", "purchase_date": "2026-03-12"}))

    assert result.domain == Domain.HOUSE and result.status == DocumentStatus.PROCESSED
    assert session.exec(select(Transaction)).all() == []
    assert session.exec(select(UtilityReading)).all() == []
    todos = {t.title: t for t in session.exec(select(Todo)).all()}
    assert "Pay Worten" not in todos
    assert todos["Paid earlier"].transaction_id is None and todos["Paid earlier"].document_id == document.id
    assert "Renew Boiler warranty" in todos


@pytest.mark.asyncio
async def test_refiling_is_refused_when_a_human_linked_a_transaction(session):
    document, transaction = _bill(session)
    commitment = Commitment(name="Boiler", cadence=Cadence.IRREGULAR, planned_amount=499.0)
    session.add(commitment)
    session.commit()
    transaction.commitment_id = commitment.id
    session.add(transaction)
    session.commit()

    with pytest.raises(RefileRefusedError, match="commitment"):
        await refile_document(session, document, Classification(Domain.HOUSE, "ownership_document", {}))
    assert session.exec(select(Transaction)).one().id == transaction.id
