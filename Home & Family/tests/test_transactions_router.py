from app.models.account import Account, AccountType
from app.models.document import Document, DocumentSource
from app.models.transaction import Category, Nature, Transaction


def _make_transaction(session, provider, category, amount, account_id=None, nature=None):
    document = Document(
        filename=f"{provider}.pdf", file_path=f"/tmp/{provider}.pdf",
        content_hash=f"hash-{provider}", source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    transaction = Transaction(
        document_id=document.id, provider=provider, category=category,
        amount=amount, currency="EUR", account_id=account_id, nature=nature,
    )
    session.add(transaction)
    session.commit()
    session.refresh(transaction)
    return transaction


def test_list_transactions_shows_all_by_default(client, session):
    _make_transaction(session, "CONTINENTE", Category.GROCERIES, 40.0)
    _make_transaction(session, "EDP", Category.ELECTRICITY, 60.0)

    response = client.get("/transactions")

    assert response.status_code == 200
    assert "CONTINENTE" in response.text
    assert "EDP" in response.text


def test_list_transactions_filters_by_category(client, session):
    _make_transaction(session, "CONTINENTE", Category.GROCERIES, 40.0)
    _make_transaction(session, "EDP", Category.ELECTRICITY, 60.0)

    response = client.get("/transactions", params={"category": "electricity"})

    assert "EDP" in response.text
    assert "CONTINENTE" not in response.text


def test_list_transactions_filters_by_nature(client, session):
    _make_transaction(session, "CONTINENTE", Category.GROCERIES, 40.0, nature=Nature.ESSENTIAL)
    _make_transaction(session, "NETFLIX", Category.SUBSCRIPTIONS, 12.99, nature=Nature.DISCRETIONARY)

    response = client.get("/transactions", params={"nature": "discretionary"})

    assert "NETFLIX" in response.text
    assert "CONTINENTE" not in response.text


def test_list_transactions_filters_by_account(client, session):
    account = Account(name="Current Account", institution="Millennium BCP", currency="EUR", account_type=AccountType.CHECKING)
    session.add(account)
    session.commit()
    session.refresh(account)

    _make_transaction(session, "CONTINENTE", Category.GROCERIES, 40.0, account_id=account.id)
    _make_transaction(session, "EDP", Category.ELECTRICITY, 60.0)

    response = client.get("/transactions", params={"account_id": account.id})

    assert "CONTINENTE" in response.text
    assert "EDP" not in response.text
