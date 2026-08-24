import re
from datetime import date
from decimal import Decimal

from sqlmodel import select

from app.models.account import Account, AccountType
from app.models.commitment import Cadence, Commitment
from app.models.debt import Debt, DebtKind
from app.models.document import Document, DocumentSource
from app.models.transaction import Category, Nature, Transaction, TransactionType


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


def test_list_transactions_paginates_100_per_page(client, session):
    for i in range(120):
        _make_transaction(session, f"SHOP {i:03d}", Category.OTHER_EXPENSE, 1.0, paid_date=date(2026, 1, 1))

    page1 = client.get("/transactions", params={"page": 1})
    page2 = client.get("/transactions", params={"page": 2})

    assert page1.status_code == 200
    assert page2.status_code == 200

    page1_ids = set(re.findall(r"SHOP \d{3}", page1.text))
    page2_ids = set(re.findall(r"SHOP \d{3}", page2.text))

    assert len(page1_ids) == 100
    assert len(page2_ids) == 20
    assert page1_ids.isdisjoint(page2_ids)


def test_list_transactions_shows_resolved_merchant_name(client, session):
    from app.models.merchant import Merchant

    merchant = Merchant(
        canonical_name="Modelo Hiper Resolved", default_category=Category.GROCERIES,
        normalized_key="modelo-hiper-resolved-router-test",
    )
    session.add(merchant)
    session.commit()
    session.refresh(merchant)

    transaction = _make_transaction(session, "MODELO HIPER 2640-MAFR", Category.GROCERIES, 40.0)
    transaction.merchant_id = merchant.id
    session.add(transaction)
    session.commit()

    response = client.get("/transactions")

    assert response.status_code == 200
    assert "Modelo Hiper Resolved" in response.text


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


def test_bulk_edit_preserves_active_filter_on_rerender(client, session):
    t1 = _make_transaction(session, "EDP", Category.ELECTRICITY, 45.0)
    t2 = _make_transaction(session, "EDP RENOVAVEIS", Category.ELECTRICITY, 55.0)
    t3 = _make_transaction(session, "CONTINENTE", Category.GROCERIES, 40.0)

    response = client.post("/transactions/bulk-edit", data={
        "transaction_ids": [str(t1.id), str(t2.id)],
        "new_nature": "essential",
        "category": "electricity",
    })

    assert response.status_code == 200
    assert "EDP" in response.text
    assert "CONTINENTE" not in response.text


def test_bulk_edit_preserves_active_page_on_rerender(client, session):
    for i in range(120):
        _make_transaction(session, f"PAGE SHOP {i:03d}", Category.OTHER_EXPENSE, 1.0, paid_date=date(2026, 1, 1))
    page2_transaction = session.exec(
        select(Transaction).where(Transaction.provider == "PAGE SHOP 000")
    ).first()

    response = client.post("/transactions/bulk-edit", data={
        "transaction_ids": [str(page2_transaction.id)],
        "new_nature": "essential",
        "page": "2",
    })

    assert response.status_code == 200
    page2_ids = set(re.findall(r"PAGE SHOP \d{3}", response.text))
    assert len(page2_ids) == 20
    assert "PAGE SHOP 000" in page2_ids


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


def test_create_commitment_from_recurring_merchant(client, session):
    from app.models.commitment import Commitment
    from app.models.merchant import Merchant

    merchant = Merchant(
        canonical_name="Netflix Router Test", default_category=Category.SUBSCRIPTIONS,
        normalized_key="netflix-router-test", confirmed=True,
    )
    session.add(merchant)
    session.commit()
    session.refresh(merchant)
    linked_transaction = _make_transaction(session, "NETFLIX ROUTER TEST", Category.SUBSCRIPTIONS, 12.99)
    linked_transaction.merchant_id = merchant.id
    session.add(linked_transaction)
    session.commit()

    response = client.post(f"/transactions/merchants/{merchant.id}/create-commitment", data={
        "cadence": "monthly", "planned_amount": "12.99",
    })

    assert response.status_code == 200
    session.refresh(merchant)
    assert merchant.recurring_reviewed is True
    commitments = session.exec(select(Commitment).where(Commitment.name == "Netflix Router Test")).all()
    assert len(commitments) == 1
    assert commitments[0].cadence.value == "monthly"
    session.refresh(linked_transaction)
    assert linked_transaction.commitment_id == commitments[0].id


def test_link_debt_creates_informal_debt_with_new_person(client, session):
    from app.models.debt import Debt

    transaction = _make_transaction(session, "TRF CRED SEPA+ P/ NEW PERSON", Category.OTHER_EXPENSE, 3000.0)

    response = client.post(f"/transactions/{transaction.id}/link-debt", data={
        "direction": "owed_to_us", "person_name": "New Person",
    })

    assert response.status_code == 200
    session.refresh(transaction)
    assert transaction.debt_candidate_reviewed is True
    assert transaction.debt_id is not None
    debt = session.get(Debt, transaction.debt_id)
    assert debt.kind.value == "informal"
    assert debt.original_amount == 3000.0


def test_link_debt_reuses_existing_person_with_same_name(client, session):
    from app.models.debt import Debt
    from app.models.person import Person

    t1 = _make_transaction(session, "TRF CRED SEPA+ P/ REPEAT PERSON", Category.OTHER_EXPENSE, 1000.0)
    t2 = _make_transaction(session, "TRF CRED SEPA+ P/ REPEAT PERSON", Category.OTHER_EXPENSE, 2000.0)

    response1 = client.post(f"/transactions/{t1.id}/link-debt", data={
        "direction": "owed_to_us", "person_name": "Repeat Person",
    })
    response2 = client.post(f"/transactions/{t2.id}/link-debt", data={
        "direction": "owed_to_us", "person_name": "Repeat Person",
    })

    assert response1.status_code == 200
    assert response2.status_code == 200

    people = session.exec(select(Person).where(Person.name == "Repeat Person")).all()
    assert len(people) == 1

    session.refresh(t1)
    session.refresh(t2)
    debt1 = session.get(Debt, t1.debt_id)
    debt2 = session.get(Debt, t2.debt_id)
    assert debt1.person_id == people[0].id
    assert debt2.person_id == people[0].id


def test_link_debt_to_existing_debt(client, session):
    from app.models.debt import Debt, DebtKind

    existing_debt = Debt(kind=DebtKind.INFORMAL, original_amount=1000.0, current_balance=Decimal("1000.00"))
    session.add(existing_debt)
    session.commit()
    session.refresh(existing_debt)

    transaction = _make_transaction(session, "TRF CRED SEPA+ P/ EXISTING PERSON", Category.OTHER_EXPENSE, 500.01)

    response = client.post(f"/transactions/{transaction.id}/link-debt", data={
        "existing_debt_id": str(existing_debt.id),
    })

    assert response.status_code == 200
    session.refresh(transaction)
    assert transaction.debt_id == existing_debt.id
    assert transaction.debt_candidate_reviewed is True


def test_list_transactions_filters_by_commitment(client, session):
    commitment = Commitment(name="IMI 2026", cadence=Cadence.YEARLY, planned_amount=600.0, year=2026)
    session.add(commitment)
    session.commit()
    session.refresh(commitment)

    t1 = _make_transaction(session, "AT IMI", Category.OTHER_EXPENSE, 300.0)
    t1.commitment_id = commitment.id
    session.add(t1)
    session.commit()
    _make_transaction(session, "EDP", Category.ELECTRICITY, 60.0)

    response = client.get("/transactions", params={"commitment_id": commitment.id})

    assert "AT IMI" in response.text
    assert "EDP" not in response.text


def test_list_transactions_filters_by_debt(client, session):
    debt = Debt(kind=DebtKind.INFORMAL, original_amount=500.0, current_balance=Decimal("500.00"))
    session.add(debt)
    session.commit()
    session.refresh(debt)

    t1 = _make_transaction(session, "TRANSFER TO JOAO", Category.TRANSFER, 500.0)
    t1.debt_id = debt.id
    session.add(t1)
    session.commit()
    _make_transaction(session, "EDP", Category.ELECTRICITY, 60.0)

    response = client.get("/transactions", params={"debt_id": debt.id})

    assert "TRANSFER TO JOAO" in response.text
    assert "EDP" not in response.text


def test_list_transactions_filters_by_transaction_type(client, session):
    _make_transaction(session, "SALARIO", Category.INCOME, 2000.0)
    _make_transaction(session, "EDP", Category.ELECTRICITY, 60.0)
    # _make_transaction defaults transaction_type to DEBIT; give SALARIO a CREDIT type directly.
    salario = session.exec(select(Transaction).where(Transaction.provider == "SALARIO")).first()
    salario.transaction_type = TransactionType.CREDIT
    session.add(salario)
    session.commit()

    response = client.get("/transactions", params={"transaction_type": "credit"})

    assert "SALARIO" in response.text
    assert "EDP" not in response.text
