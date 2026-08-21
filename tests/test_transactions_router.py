from datetime import date

from app.models.account import Account, AccountType
from app.models.document import Document, DocumentSource
from app.models.transaction import Category, Nature, Transaction


def _make_transaction(session, provider, category, amount, account_id=None, nature=None, paid_date=None):
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
        paid_date=paid_date,
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


def test_list_transactions_filters_by_date_range(client, session):
    _make_transaction(session, "CONTINENTE", Category.GROCERIES, 40.0, paid_date=date(2026, 6, 15))
    _make_transaction(session, "EDP", Category.ELECTRICITY, 60.0, paid_date=date(2026, 1, 5))

    response = client.get("/transactions", params={"date_from": "2026-06-01", "date_to": "2026-06-30"})

    assert "CONTINENTE" in response.text
    assert "EDP" not in response.text


def test_list_transactions_combines_category_and_nature_filters(client, session):
    _make_transaction(session, "CONTINENTE", Category.GROCERIES, 40.0, nature=Nature.ESSENTIAL)
    _make_transaction(session, "NETFLIX", Category.SUBSCRIPTIONS, 12.99, nature=Nature.DISCRETIONARY)
    _make_transaction(session, "GYM", Category.SUBSCRIPTIONS, 30.0, nature=Nature.ESSENTIAL)

    response = client.get("/transactions", params={"category": "subscriptions", "nature": "discretionary"})

    assert "NETFLIX" in response.text
    assert "GYM" not in response.text
    assert "CONTINENTE" not in response.text


def test_bulk_edit_applies_category_to_selected_transactions(client, session):
    t1 = _make_transaction(session, "SHOP A", Category.OTHER_EXPENSE, 10.0)
    t2 = _make_transaction(session, "SHOP B", Category.OTHER_EXPENSE, 20.0)
    t3 = _make_transaction(session, "SHOP C", Category.OTHER_EXPENSE, 30.0)

    response = client.post("/transactions/bulk-edit", data={
        "transaction_ids": [str(t1.id), str(t2.id)],
        "new_category": "shopping",
    })

    assert response.status_code == 200
    session.refresh(t1)
    session.refresh(t2)
    session.refresh(t3)
    assert t1.category == Category.SHOPPING
    assert t2.category == Category.SHOPPING
    assert t3.category == Category.OTHER_EXPENSE


def test_bulk_edit_applies_nature_and_account(client, session):
    account = Account(name="Savings", institution="Millennium BCP", currency="EUR", account_type=AccountType.SAVINGS)
    session.add(account)
    session.commit()
    session.refresh(account)

    t1 = _make_transaction(session, "SHOP D", Category.SHOPPING, 10.0)

    response = client.post("/transactions/bulk-edit", data={
        "transaction_ids": [str(t1.id)],
        "new_nature": "discretionary",
        "new_account_id": str(account.id),
    })

    assert response.status_code == 200
    session.refresh(t1)
    assert t1.nature == Nature.DISCRETIONARY
    assert t1.account_id == account.id


def test_bulk_edit_with_no_selection_changes_nothing(client, session):
    t1 = _make_transaction(session, "SHOP E", Category.OTHER_EXPENSE, 10.0)

    response = client.post("/transactions/bulk-edit", data={"new_category": "shopping"})

    assert response.status_code == 200
    session.refresh(t1)
    assert t1.category == Category.OTHER_EXPENSE
