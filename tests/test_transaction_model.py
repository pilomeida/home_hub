from app.models.document import Document, DocumentSource
from app.models.transaction import Category, Transaction


def test_create_and_read_transaction(session):
    document = Document(
        filename="edp-august.pdf", file_path="/tmp/edp.pdf",
        content_hash="hash1", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id,
        provider="EDP",
        category=Category.ELECTRICITY,
        amount=87.32,
        currency="EUR",
        statement_period="2026-08",
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    assert transaction.id is not None
    assert transaction.category == Category.ELECTRICITY
    assert transaction.currency == "EUR"
