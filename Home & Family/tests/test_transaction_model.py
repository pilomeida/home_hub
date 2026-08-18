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


from app.models.transaction import TransactionType


def test_transaction_type_defaults_to_debit(session):
    document = Document(
        filename="edp-august.pdf", file_path="/tmp/edp2.pdf",
        content_hash="hash2", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="EDP", category=Category.ELECTRICITY,
        amount=87.32, currency="EUR", statement_period="2026-08",
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    assert transaction.transaction_type == TransactionType.DEBIT


def test_transaction_supports_new_category_and_credit_type(session):
    document = Document(
        filename="statement.pdf", file_path="/tmp/statement.pdf",
        content_hash="hash3", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="SALARIO EMPRESA X", category=Category.INCOME,
        transaction_type=TransactionType.CREDIT, amount=2200.0, currency="EUR",
        statement_period="2026-07",
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    assert transaction.category == Category.INCOME
    assert transaction.transaction_type == TransactionType.CREDIT
