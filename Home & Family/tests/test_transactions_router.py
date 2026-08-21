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


def _make_unconfirmed_merchant(session, name="New Shop", key="new-shop-router-test"):
    from app.models.merchant import Merchant
    merchant = Merchant(canonical_name=name, default_category=Category.SHOPPING, normalized_key=key)
    session.add(merchant)
    session.commit()
    session.refresh(merchant)
    return merchant


def test_needs_review_page_lists_unconfirmed_merchant(client, session):
    merchant = _make_unconfirmed_merchant(session)

    response = client.get("/transactions/needs-review")

    assert response.status_code == 200
    assert merchant.canonical_name in response.text


def test_confirm_merchant_removes_it_from_queue(client, session):
    merchant = _make_unconfirmed_merchant(session, name="Confirm Me", key="confirm-me-router-test")

    response = client.post(f"/transactions/merchants/{merchant.id}/confirm")

    assert response.status_code == 200
    assert "Confirm Me" not in response.text
    session.refresh(merchant)
    assert merchant.confirmed is True


def test_dismiss_recurring_marks_merchant_reviewed(client, session):
    from app.models.merchant import Merchant
    merchant = Merchant(
        canonical_name="Recurring Test", default_category=Category.SUBSCRIPTIONS,
        normalized_key="recurring-test-router", confirmed=True,
    )
    session.add(merchant)
    session.commit()
    session.refresh(merchant)
    for i, period in enumerate(["2026-05", "2026-06", "2026-07"]):
        t = _make_transaction(session, "RECURRING TEST", Category.SUBSCRIPTIONS, 9.99)
        t.merchant_id = merchant.id
        t.statement_period = period
        session.add(t)
    session.commit()

    response = client.post(f"/transactions/merchants/{merchant.id}/dismiss-recurring")

    assert response.status_code == 200
    session.refresh(merchant)
    assert merchant.recurring_reviewed is True


def test_dismiss_debt_candidate_marks_transaction_reviewed(client, session):
    transaction = _make_transaction(
        session, "TRF CRED SEPA+ P/ SOME PERSON", Category.OTHER_EXPENSE, 600.0
    )

    response = client.post(f"/transactions/{transaction.id}/dismiss-debt-candidate")

    assert response.status_code == 200
    session.refresh(transaction)
    assert transaction.debt_candidate_reviewed is True
