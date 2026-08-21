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


def test_transaction_links_to_account_commitment_and_debt(session):
    from app.models.account import Account, AccountType
    from app.models.commitment import Cadence, Commitment
    from app.models.debt import Debt, DebtKind
    from app.models.transaction import Nature

    document = Document(
        filename="imi-instalment-1.pdf", file_path="/tmp/imi1.pdf",
        content_hash="hash-imi-1", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    account = Account(name="Current Account", institution="Millennium BCP", currency="EUR", account_type=AccountType.CHECKING)
    commitment = Commitment(name="IMI 2026", category=Category.HOME, cadence=Cadence.YEARLY, planned_amount=4800.0, year=2026)
    debt = Debt(kind=DebtKind.FORMAL, original_amount=142300.0, current_balance=142300.0)
    session.add_all([account, commitment, debt])
    session.commit()
    session.refresh(account)
    session.refresh(commitment)
    session.refresh(debt)

    transaction = Transaction(
        document_id=document.id,
        provider="AT - IMI",
        category=Category.HOME,
        amount=1600.0,
        currency="EUR",
        account_id=account.id,
        commitment_id=commitment.id,
        debt_id=debt.id,
        nature=Nature.ESSENTIAL,
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    fetched = session.get(Transaction, transaction.id)
    assert fetched.account_id == account.id
    assert fetched.commitment_id == commitment.id
    assert fetched.debt_id == debt.id
    assert fetched.nature == Nature.ESSENTIAL


def test_transaction_new_fields_default_to_none(session):
    document = Document(
        filename="groceries.pdf", file_path="/tmp/groceries.pdf",
        content_hash="hash-groceries-1", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)

    transaction = Transaction(
        document_id=document.id, provider="CONTINENTE", category=Category.GROCERIES,
        amount=45.20, currency="EUR",
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)

    assert transaction.account_id is None
    assert transaction.commitment_id is None
    assert transaction.debt_id is None
    assert transaction.nature is None
