import pytest
import re
from datetime import date
from decimal import Decimal

from sqlmodel import select

from app.models.account import Account, AccountType
from app.models.commitment import Cadence, Commitment
from app.models.debt import Debt, DebtDirection, DebtKind
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

    response = client.get("/financials/transactions")

    assert response.status_code == 200
    assert "CONTINENTE" in response.text
    assert "EDP" in response.text


def test_list_transactions_filters_by_category(client, session):
    _make_transaction(session, "CONTINENTE", Category.GROCERIES, 40.0)
    _make_transaction(session, "EDP", Category.ELECTRICITY, 60.0)

    response = client.get("/financials/transactions", params={"category": "electricity"})

    assert "EDP" in response.text
    assert "CONTINENTE" not in response.text


def test_list_transactions_filters_by_nature(client, session):
    _make_transaction(session, "CONTINENTE", Category.GROCERIES, 40.0, nature=Nature.ESSENTIAL)
    _make_transaction(session, "NETFLIX", Category.SUBSCRIPTIONS, 12.99, nature=Nature.DISCRETIONARY)

    response = client.get("/financials/transactions", params={"nature": "discretionary"})

    assert "NETFLIX" in response.text
    assert "CONTINENTE" not in response.text


def test_list_transactions_filters_by_account(client, session):
    account = Account(name="Current Account", institution="Millennium BCP", currency="EUR", account_type=AccountType.CHECKING)
    session.add(account)
    session.commit()
    session.refresh(account)

    _make_transaction(session, "CONTINENTE", Category.GROCERIES, 40.0, account_id=account.id)
    _make_transaction(session, "EDP", Category.ELECTRICITY, 60.0)

    response = client.get("/financials/transactions", params={"account_id": account.id})

    assert "CONTINENTE" in response.text
    assert "EDP" not in response.text


def test_list_transactions_filters_by_date_range(client, session):
    _make_transaction(session, "CONTINENTE", Category.GROCERIES, 40.0, paid_date=date(2026, 6, 15))
    _make_transaction(session, "EDP", Category.ELECTRICITY, 60.0, paid_date=date(2026, 1, 5))

    response = client.get("/financials/transactions", params={"date_from": "2026-06-01", "date_to": "2026-06-30"})

    assert "CONTINENTE" in response.text
    assert "EDP" not in response.text


def test_list_transactions_combines_category_and_nature_filters(client, session):
    _make_transaction(session, "CONTINENTE", Category.GROCERIES, 40.0, nature=Nature.ESSENTIAL)
    _make_transaction(session, "NETFLIX", Category.SUBSCRIPTIONS, 12.99, nature=Nature.DISCRETIONARY)
    _make_transaction(session, "GYM", Category.SUBSCRIPTIONS, 30.0, nature=Nature.ESSENTIAL)

    response = client.get("/financials/transactions", params={"category": "subscriptions", "nature": "discretionary"})

    assert "NETFLIX" in response.text
    assert "GYM" not in response.text
    assert "CONTINENTE" not in response.text


def test_list_transactions_paginates_100_per_page(client, session):
    for i in range(120):
        _make_transaction(session, f"SHOP {i:03d}", Category.OTHER_EXPENSE, 1.0, paid_date=date(2026, 1, 1))

    page1 = client.get("/financials/transactions", params={"page": 1})
    page2 = client.get("/financials/transactions", params={"page": 2})

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

    response = client.get("/financials/transactions")

    assert response.status_code == 200
    assert "Modelo Hiper Resolved" in response.text


def test_bulk_edit_applies_category_to_selected_transactions(client, session):
    from app.services.taxonomy import ensure_taxonomy, get_node
    ensure_taxonomy(session)
    t1 = _make_transaction(session, "SHOP A", Category.OTHER_EXPENSE, 10.0)
    t2 = _make_transaction(session, "SHOP B", Category.OTHER_EXPENSE, 20.0)
    t3 = _make_transaction(session, "SHOP C", Category.OTHER_EXPENSE, 30.0)

    response = client.post("/financials/transactions/bulk-edit", data={
        "transaction_ids": [str(t1.id), str(t2.id)],
        "new_category": "shopping",
    })

    assert response.status_code == 200
    session.refresh(t1)
    session.refresh(t2)
    session.refresh(t3)
    assert t1.category == Category.SHOPPING
    assert t2.category == Category.SHOPPING
    assert t1.category_id == t2.category_id == get_node(session, "personal-lifestyle.personal.general-shopping").id
    assert t3.category == Category.OTHER_EXPENSE


def test_bulk_edit_applies_nature_and_account(client, session):
    account = Account(name="Savings", institution="Millennium BCP", currency="EUR", account_type=AccountType.SAVINGS)
    session.add(account)
    session.commit()
    session.refresh(account)

    t1 = _make_transaction(session, "SHOP D", Category.SHOPPING, 10.0)

    response = client.post("/financials/transactions/bulk-edit", data={
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

    response = client.post("/financials/transactions/bulk-edit", data={"new_category": "shopping"})

    assert response.status_code == 200
    session.refresh(t1)
    assert t1.category == Category.OTHER_EXPENSE


def test_bulk_edit_preserves_active_filter_on_rerender(client, session):
    t1 = _make_transaction(session, "EDP", Category.ELECTRICITY, 45.0)
    t2 = _make_transaction(session, "EDP RENOVAVEIS", Category.ELECTRICITY, 55.0)
    t3 = _make_transaction(session, "CONTINENTE", Category.GROCERIES, 40.0)

    response = client.post("/financials/transactions/bulk-edit", data={
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

    response = client.post("/financials/transactions/bulk-edit", data={
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

    response = client.get("/financials/transactions/needs-review")

    assert response.status_code == 200
    assert f'href="/financials/transactions?merchant_id={merchant.id}"' in response.text
    assert merchant.canonical_name in response.text


def test_confirm_merchant_removes_it_from_queue(client, session):
    from app.services.taxonomy import ensure_taxonomy
    ensure_taxonomy(session)
    merchant = _make_unconfirmed_merchant(session, name="Confirm Me", key="confirm-me-router-test")

    response = client.post(f"/financials/transactions/merchants/{merchant.id}/confirm",
                           data={"category_node": "food.groceries.supermarket"})

    assert response.status_code == 200
    assert "Confirm Me" not in response.text
    session.refresh(merchant)
    assert merchant.confirmed is True


def test_confirm_merchant_without_a_category_does_not_confirm(client, session):
    from app.services.taxonomy import ensure_taxonomy
    ensure_taxonomy(session)
    merchant = _make_unconfirmed_merchant(session, name="Still Open", key="still-open-router-test")

    response = client.post(f"/financials/transactions/merchants/{merchant.id}/confirm", data={})

    assert response.status_code == 200
    assert "Still Open" in response.text
    session.refresh(merchant)
    assert merchant.confirmed is False


def test_confirm_merchant_does_not_file_credits_under_an_out_node(client, session):
    from app.models.document import Document, DocumentSource
    from app.models.transaction import Transaction
    from app.services.taxonomy import ensure_taxonomy, get_node
    ensure_taxonomy(session)
    merchant = _make_unconfirmed_merchant(session, name="Shop2", key="shop2-router-test")
    doc = Document(filename="s.pdf", file_path="/tmp/s2.pdf", content_hash="h-shop2", source=DocumentSource.MANUAL)
    session.add(doc); session.commit()
    deb = Transaction(document_id=doc.id, provider="S", category=Category.OTHER, amount=1.0, merchant_id=merchant.id)
    cred = Transaction(document_id=doc.id, provider="S", category=Category.OTHER, amount=1.0,
                       merchant_id=merchant.id, transaction_type=TransactionType.CREDIT)
    session.add(deb); session.add(cred); session.commit()
    client.post(f"/financials/transactions/merchants/{merchant.id}/confirm",
                data={"category_node": "food.groceries.supermarket"})
    session.refresh(deb); session.refresh(cred)
    assert deb.category_id == get_node(session, "food.groceries.supermarket").id
    assert cred.category_id is None


def test_confirm_merchant_applies_edited_category_node_and_nature(client, session):
    from app.services.taxonomy import ensure_taxonomy, get_node
    ensure_taxonomy(session)
    merchant = _make_unconfirmed_merchant(session, name="Edit Me", key="edit-me-router-test")

    response = client.post(
        f"/financials/transactions/merchants/{merchant.id}/confirm",
        data={"category_node": "food.dining-out.restaurants", "nature": "discretionary"},
    )

    assert response.status_code == 200
    session.refresh(merchant)
    assert merchant.confirmed is True
    assert merchant.default_category_id == get_node(session, "food.dining-out.restaurants").id
    assert merchant.default_category == Category.RESTAURANTS
    assert merchant.default_nature == Nature.DISCRETIONARY


def test_confirm_merchant_files_every_unsorted_transaction_of_that_merchant(client, session):
    from app.models.document import Document, DocumentSource
    from app.models.transaction import Transaction
    from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy, file_transaction, get_node
    ensure_taxonomy(session)
    merchant = _make_unconfirmed_merchant(session, name="Galp", key="galp-router-test")
    other = _make_unconfirmed_merchant(session, name="Other", key="other-router-test")
    doc = Document(filename="g.pdf", file_path="/tmp/g.pdf", content_hash="h-galp", source=DocumentSource.MANUAL)
    session.add(doc)
    session.commit()
    fuel = get_node(session, "transport.car-running.fuel")
    groceries = get_node(session, "food.groceries.supermarket")

    def mk(m, node):
        t = Transaction(document_id=doc.id, provider="GALP", category=Category.OTHER, amount=10.0, merchant_id=m.id)
        session.add(t)
        session.commit()
        if node is not None:
            file_transaction(session, t, node)
        session.commit()
        return t

    unfiled, in_unsorted = mk(merchant, None), mk(merchant, get_node(session, UNSORTED_SLUG))
    already_filed, others = mk(merchant, groceries), mk(other, None)

    response = client.post(
        f"/financials/transactions/merchants/{merchant.id}/confirm",
        data={"category_node": "transport.car-running.fuel"},
    )

    assert response.status_code == 200
    for t in (unfiled, in_unsorted, already_filed, others):
        session.refresh(t)
    assert unfiled.category_id == fuel.id and in_unsorted.category_id == fuel.id
    assert already_filed.category_id == groceries.id  # a deliberate filing is never overwritten
    assert others.category_id is None


def test_needs_review_page_shows_node_and_nature_dropdowns(client, session):
    from app.services.taxonomy import ensure_taxonomy
    ensure_taxonomy(session)
    _make_unconfirmed_merchant(session, name="Dropdown Test", key="dropdown-test-router")

    response = client.get("/financials/transactions/needs-review")

    assert response.status_code == 200
    assert '<select name="category_node"' in response.text
    assert '<select name="nature">' in response.text
    # The full list is loaded on first focus, not repeated in every row.
    assert 'hx-get="/financials/transactions/node-options' in response.text
    assert 'value="food.groceries.supermarket"' not in response.text
    options = client.get("/financials/transactions/node-options").text
    assert 'value="food.groceries.supermarket"' in options
    assert "Food › Groceries › Supermarket" in options
    assert 'value="food"' not in options  # only level-3 nodes


def test_needs_review_page_lists_unsorted_transactions_grouped_by_merchant(client, session):
    from app.models.document import Document, DocumentSource
    from app.models.merchant import Merchant
    from app.models.transaction import Transaction
    from app.services.taxonomy import ensure_taxonomy
    ensure_taxonomy(session)
    m = Merchant(canonical_name="Padaria Zeta", normalized_key="padaria-zeta", confirmed=True)
    session.add(m)
    session.commit()
    doc = Document(filename="p.pdf", file_path="/tmp/p.pdf", content_hash="h-pz", source=DocumentSource.MANUAL)
    session.add(doc)
    session.commit()
    for i in range(2):
        session.add(Transaction(document_id=doc.id, provider=f"PADARIA ZETA {i}", category=Category.OTHER,
                                amount=3.0, merchant_id=m.id))
    session.commit()

    response = client.get("/financials/transactions/needs-review")

    assert "Padaria Zeta" in response.text
    assert "2 transactions" in response.text


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

    response = client.post(f"/financials/transactions/merchants/{merchant.id}/dismiss-recurring")

    assert response.status_code == 200
    session.refresh(merchant)
    assert merchant.recurring_reviewed is True


def test_dismiss_debt_candidate_marks_transaction_reviewed(client, session):
    transaction = _make_transaction(
        session, "TRF CRED SEPA+ P/ SOME PERSON", Category.OTHER_EXPENSE, 600.0
    )

    response = client.post(f"/financials/transactions/{transaction.id}/dismiss-debt-candidate")

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

    response = client.post(f"/financials/transactions/merchants/{merchant.id}/create-commitment", data={
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

    response = client.post(f"/financials/transactions/{transaction.id}/link-debt", data={
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

    response1 = client.post(f"/financials/transactions/{t1.id}/link-debt", data={
        "direction": "owed_to_us", "person_name": "Repeat Person",
    })
    response2 = client.post(f"/financials/transactions/{t2.id}/link-debt", data={
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


def test_list_transactions_filters_by_transaction_id(client, session):
    t1 = _make_transaction(session, "SHOP F", Category.OTHER_EXPENSE, 10.0)
    t2 = _make_transaction(session, "SHOP G", Category.OTHER_EXPENSE, 20.0)

    response = client.get("/financials/transactions", params={"transaction_id": t1.id})

    assert response.status_code == 200
    assert "SHOP F" in response.text
    assert "SHOP G" not in response.text


def test_list_transactions_filters_by_merchant_id(client, session):
    from app.models.merchant import Merchant

    merchant = Merchant(
        canonical_name="Merchant Filter Test", default_category=Category.SHOPPING,
        normalized_key="merchant-filter-test-router",
    )
    session.add(merchant)
    session.commit()
    session.refresh(merchant)

    t1 = _make_transaction(session, "SHOP H", Category.SHOPPING, 10.0)
    t2 = _make_transaction(session, "SHOP I", Category.SHOPPING, 20.0)
    t1.merchant_id = merchant.id
    session.add(t1)
    session.commit()

    response = client.get("/financials/transactions", params={"merchant_id": merchant.id})

    assert response.status_code == 200
    assert "SHOP H" in response.text
    assert "SHOP I" not in response.text
    assert "Merchant Filter Test" in response.text
    assert f'href="/financials/transactions?merchant_id={merchant.id}"' in response.text


def test_list_transactions_shows_linked_transaction(client, session):
    account_a = Account(name="Checking", institution="Bank A", currency="EUR", account_type=AccountType.CHECKING)
    account_b = Account(name="Wallet", institution="Bank B", currency="EUR", account_type=AccountType.CHECKING)
    session.add(account_a)
    session.add(account_b)
    session.commit()
    session.refresh(account_a)
    session.refresh(account_b)

    outgoing = _make_transaction(
        session, "TRANSFER OUT", Category.TRANSFER, 100.0,
        account_id=account_a.id, paid_date=date(2026, 3, 5),
    )
    incoming = _make_transaction(
        session, "TRANSFER IN", Category.TRANSFER, 100.0,
        account_id=account_b.id, paid_date=date(2026, 3, 3),
    )
    outgoing.linked_transaction_id = incoming.id
    incoming.linked_transaction_id = outgoing.id
    session.add(outgoing)
    session.add(incoming)
    session.commit()

    response = client.get("/financials/transactions")

    assert response.status_code == 200
    assert f'/financials/transactions?transaction_id={incoming.id}' in response.text
    assert "Wallet" in response.text


def test_link_debt_to_existing_debt(client, session):
    from app.models.debt import Debt, DebtKind

    existing_debt = Debt(kind=DebtKind.INFORMAL, original_amount=1000.0, current_balance=Decimal("1000.00"))
    session.add(existing_debt)
    session.commit()
    session.refresh(existing_debt)

    transaction = _make_transaction(session, "TRF CRED SEPA+ P/ EXISTING PERSON", Category.OTHER_EXPENSE, 500.01)

    response = client.post(f"/financials/transactions/{transaction.id}/link-debt", data={
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

    response = client.get("/financials/transactions", params={"commitment_id": commitment.id})

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

    response = client.get("/financials/transactions", params={"debt_id": debt.id})

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

    response = client.get("/financials/transactions", params={"transaction_type": "credit"})

    assert "SALARIO" in response.text
    assert "EDP" not in response.text


from app.services.taxonomy import ensure_taxonomy, file_transaction, get_node


def _filed(session, provider, slug, amount=10.0, ttype=TransactionType.DEBIT):
    t = _make_transaction(session, provider, Category.OTHER, amount)
    t.transaction_type = ttype
    file_transaction(session, t, get_node(session, slug))
    session.commit()
    return t


def test_list_shows_tree_path_and_flow_class(client, session):
    ensure_taxonomy(session)
    _filed(session, "PINGO DOCE", "food.groceries.supermarket")
    _filed(session, "RENT IN", "income.rental.house-rent", ttype=TransactionType.CREDIT)
    html = client.get("/financials/transactions").text
    assert "Food › Groceries › Supermarket" in html
    assert 'class="flow-out"' in html and 'class="flow-in"' in html


def test_filter_by_node_includes_descendants(client, session):
    ensure_taxonomy(session)
    _filed(session, "PINGO DOCE", "food.groceries.supermarket")
    _filed(session, "GALP", "transport.car-running.fuel")
    html = client.get("/financials/transactions?category_node=food").text
    assert "PINGO DOCE" in html and "GALP" not in html


def test_bulk_edit_files_under_node_and_dual_writes(client, session):
    ensure_taxonomy(session)
    t = _make_transaction(session, "EDP", Category.OTHER, 60.0)
    client.post("/financials/transactions/bulk-edit",
                data={"transaction_ids": [str(t.id)], "new_category_node": "housing.utilities.electricity"})
    session.refresh(t)
    assert t.category_id == get_node(session, "housing.utilities.electricity").id
    assert t.category == Category.ELECTRICITY


def test_bulk_edit_unknown_node_slug_is_400(client, session):
    from app.services.taxonomy import ensure_taxonomy
    ensure_taxonomy(session)
    r = client.post("/financials/transactions/bulk-edit",
                    data={"transaction_ids": ["1"], "new_category_node": "made.up.slug"})
    assert r.status_code == 400


def test_bulk_edit_legacy_category_keeps_category_id_consistent(client, session):
    from app.models.document import Document, DocumentSource
    from app.services.taxonomy import ensure_taxonomy, get_node
    ensure_taxonomy(session)
    doc = Document(filename="b.pdf", file_path="/tmp/b.pdf", content_hash="h-bulk", source=DocumentSource.MANUAL)
    session.add(doc); session.commit()
    t = Transaction(document_id=doc.id, provider="B", category=Category.OTHER, amount=1.0)
    session.add(t); session.commit(); session.refresh(t)
    ok = client.post("/financials/transactions/bulk-edit",
                     data={"transaction_ids": [str(t.id)], "new_category": "groceries"})
    assert ok.status_code == 200
    session.refresh(t)
    assert t.category == Category.GROCERIES
    assert t.category_id == get_node(session, "food.groceries.supermarket").id
    ambiguous = client.post("/financials/transactions/bulk-edit",
                            data={"transaction_ids": [str(t.id)], "new_category": "other_expense"})
    assert ambiguous.status_code == 400


def test_needs_review_survives_a_transaction_whose_merchant_is_missing(client, session):
    from app.models.document import Document, DocumentSource
    from app.services.taxonomy import ensure_taxonomy
    ensure_taxonomy(session)
    doc = Document(filename="o.pdf", file_path="/tmp/o.pdf", content_hash="h-orphan", source=DocumentSource.MANUAL)
    session.add(doc); session.commit()
    session.add(Transaction(document_id=doc.id, provider="ORPHAN", category=Category.OTHER,
                            amount=1.0, merchant_id=987654))
    session.commit()
    r = client.get("/financials/transactions/needs-review")
    assert r.status_code == 200


def test_link_debt_creates_match_rule_and_balance(client, session):
    from app.models.position import DebtMatchRule
    from app.services.classification_engine import normalize_provider

    txn = _make_transaction(session, "TRF SEPA+ P/ SYNTH PERSON", Category.OTHER_EXPENSE, 400.0)
    client.post(f"/financials/transactions/{txn.id}/link-debt", data={"direction": "owed_to_us", "person_name": "Synth Person"})
    session.refresh(txn)
    debt = session.get(Debt, txn.debt_id)
    rule = session.exec(select(DebtMatchRule)).one()
    assert rule.debt_id == debt.id
    assert rule.normalized_key == normalize_provider("TRF SEPA+ P/ SYNTH PERSON")
    assert debt.current_balance == Decimal("400.00")


def test_link_debt_to_existing_debt_creates_rule_and_recomputes(client, session):
    from app.models.debt import DebtDirection
    from app.models.position import DebtMatchRule

    from app.services.debt_ledger import create_informal_debt
    debt = create_informal_debt(session, "Synth Other", DebtDirection.OWED_BY_US, opening_amount=1000.0)
    session.commit()
    session.refresh(debt)
    txn = _make_transaction(session, "TRF SEPA+ P/ SYNTH OTHER", Category.OTHER_EXPENSE, 250.0)
    client.post(f"/financials/transactions/{txn.id}/link-debt", data={"existing_debt_id": str(debt.id)})
    session.refresh(debt)
    assert session.exec(select(DebtMatchRule)).one().debt_id == debt.id
    assert debt.current_balance == Decimal("750.00")


def test_link_debt_never_creates_rule_for_statement_loan(client, session):
    from app.models.position import DebtMatchRule

    loan = Debt(kind=DebtKind.FORMAL, original_amount=1000.0, current_balance=Decimal("1000.00"), external_number="987654")
    session.add(loan)
    session.commit()
    session.refresh(loan)
    txn = _make_transaction(session, "SOME DEBIT", Category.OTHER_EXPENSE, 10.0)
    client.post(f"/financials/transactions/{txn.id}/link-debt", data={"existing_debt_id": str(loan.id)})
    assert session.exec(select(DebtMatchRule)).first() is None


@pytest.mark.asyncio
async def test_second_matching_transaction_auto_links_and_lowers_balance(client, session):
    from app.models.debt import DebtDirection
    from app.models.merchant import Merchant
    from app.services.classification_engine import classify_transaction, normalize_provider
    from tests.fakes.fake_gateway import FakeGateway  # noqa: F401

    first = _make_transaction(session, "TRF SEPA+ P/ SYNTH REPEAT", Category.OTHER_EXPENSE, 400.0)
    client.post(f"/financials/transactions/{first.id}/link-debt", data={"direction": "owed_to_us", "person_name": "Synth Repeat"})
    session.refresh(first)
    debt_id = first.debt_id
    session.add(Merchant(canonical_name="Synth Repeat", default_category=Category.OTHER_EXPENSE,
                         default_nature=Nature.ESSENTIAL, normalized_key=normalize_provider("TRF SEPA+ P/ SYNTH REPEAT")))
    session.commit()

    second = _make_transaction(session, "TRF SEPA+ P/ SYNTH REPEAT", Category.OTHER_EXPENSE, 100.0)
    second.transaction_type = TransactionType.CREDIT  # they pay us back
    session.add(second)
    session.commit()
    await classify_transaction(session, second, gateway=None)
    session.commit()
    session.refresh(second)
    debt = session.get(Debt, debt_id)
    assert second.debt_id == debt_id
    assert second.debt_candidate_reviewed is True
    assert debt.current_balance == Decimal("300.00")


# --- Block D: informal loans as a ledger ---------------------------------

def _mk(session, provider, amount, ttype=TransactionType.DEBIT, paid=None):
    t = _make_transaction(session, provider, Category.OTHER_EXPENSE, amount, paid_date=paid)
    t.transaction_type = ttype
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _entries(session):
    from app.models.position import DebtEntry
    return session.exec(select(DebtEntry).order_by(DebtEntry.id)).all()


def test_link_debt_new_debt_starts_with_the_transaction_as_first_advance(client, session):
    t = _mk(session, "TRF SEPA+ P/ TEST PERSON", 1000.0)
    r = client.post(f"/financials/transactions/{t.id}/link-debt", data={"direction": "owed_to_us", "person_name": "Test Person"})
    assert r.status_code == 200
    session.refresh(t)
    debt = session.get(Debt, t.debt_id)
    (entry,) = _entries(session)
    assert (entry.kind, entry.amount, entry.transaction_id, entry.debt_id) == ("advance", 1000.0, t.id, debt.id)
    assert debt.current_balance == Decimal("1000.00") and debt.original_amount == 1000.0


def test_link_debt_derives_the_direction_when_missing(client, session):
    out = _mk(session, "TRF SEPA+ P/ TEST A", 700.0, TransactionType.DEBIT)
    inn = _mk(session, "TRF SEPA+ P/ TEST B", 800.0, TransactionType.CREDIT)
    client.post(f"/financials/transactions/{out.id}/link-debt", data={"person_name": "Test A"})
    client.post(f"/financials/transactions/{inn.id}/link-debt", data={"person_name": "Test B"})
    session.refresh(out); session.refresh(inn)
    assert session.get(Debt, out.debt_id).direction.value == "owed_to_us"
    assert session.get(Debt, inn.debt_id).direction.value == "owed_by_us"
    assert {e.kind for e in _entries(session)} == {"advance"}


def test_link_debt_transfer_type_needs_direction_and_role(client, session):
    t = _mk(session, "TRF MBWAY TEST PERSON", 600.0, TransactionType.TRANSFER)
    r = client.post(f"/financials/transactions/{t.id}/link-debt", data={"person_name": "Test Person"})
    assert r.status_code == 400 and "direction" in r.text.lower()
    # existing debt: role is required
    from app.services.debt_ledger import create_informal_debt
    debt = create_informal_debt(session, "Test Person", DebtDirection.OWED_TO_US, opening_amount=1000.0)
    session.commit()
    r = client.post(f"/financials/transactions/{t.id}/link-debt", data={"existing_debt_id": str(debt.id)})
    assert r.status_code == 400 and "role" in r.text.lower()
    session.refresh(t)
    assert t.debt_id is None
    r = client.post(f"/financials/transactions/{t.id}/link-debt", data={"existing_debt_id": str(debt.id), "role": "repayment"})
    assert r.status_code == 200
    session.refresh(debt)
    assert debt.current_balance == Decimal("400.00")
    # a new debt from a TRANSFER-type transaction with a direction works
    t2 = _mk(session, "TRF MBWAY OTHER PERSON", 90.0, TransactionType.TRANSFER)
    r = client.post(f"/financials/transactions/{t2.id}/link-debt", data={"person_name": "Other Person", "direction": "owed_by_us"})
    assert r.status_code == 200


def test_link_debt_role_override_and_invalid_role(client, session):
    from app.services.debt_ledger import create_informal_debt
    debt = create_informal_debt(session, "Test Person", DebtDirection.OWED_TO_US, opening_amount=1000.0)
    session.commit()
    t = _mk(session, "TRF SEPA+ P/ TEST PERSON", 100.0, TransactionType.DEBIT)  # would be an advance
    bad = client.post(f"/financials/transactions/{t.id}/link-debt", data={"existing_debt_id": str(debt.id), "role": "adjust_up"})
    assert bad.status_code == 400
    ok = client.post(f"/financials/transactions/{t.id}/link-debt", data={"existing_debt_id": str(debt.id), "role": "repayment"})
    assert ok.status_code == 200
    session.refresh(debt)
    assert debt.current_balance == Decimal("900.00")


def test_link_debt_to_existing_debt_via_select_adds_an_advance(client, session):
    from app.services.debt_ledger import create_informal_debt
    debt = create_informal_debt(session, "Test Person", DebtDirection.OWED_TO_US, opening_amount=1000.0)
    session.commit()
    t = _mk(session, "TRF SEPA+ P/ TEST PERSON", 500.0, TransactionType.DEBIT)
    client.post(f"/financials/transactions/{t.id}/link-debt", data={"existing_debt_id": str(debt.id), "role": ""})
    session.refresh(debt)
    assert debt.current_balance == Decimal("1500.00")


def test_link_debt_refuses_a_transaction_already_on_another_debt(client, session):
    from app.services.debt_ledger import create_informal_debt, link_transaction_as_entry
    a = create_informal_debt(session, "Test A", DebtDirection.OWED_TO_US)
    b = create_informal_debt(session, "Test B", DebtDirection.OWED_TO_US)
    t = _mk(session, "TRF SEPA+ P/ TEST A", 50.0)
    link_transaction_as_entry(session, a, t, None)
    session.commit()
    r = client.post(f"/financials/transactions/{t.id}/link-debt", data={"existing_debt_id": str(b.id)})
    assert r.status_code == 400
    session.refresh(t)
    assert t.debt_id == a.id


def test_needs_review_shows_derived_role_and_informal_debt_select(client, session):
    from app.services.debt_ledger import create_informal_debt
    debt = create_informal_debt(session, "Test Person", DebtDirection.OWED_TO_US, opening_amount=1000.0)
    session.commit()
    out = _mk(session, "TRF SEPA+ P/ TEST PERSON", 900.0, TransactionType.DEBIT)
    tr = _mk(session, "TRF MBWAY P/ TEST OTHER", 700.0, TransactionType.TRANSFER)
    html = client.get("/financials/transactions/needs-review").text
    assert 'name="existing_debt_id"' in html and 'type="number" name="existing_debt_id"' not in html
    assert f'<option value="{debt.id}"' in html and "Test Person" in html and "1,000.00" in html
    assert 'name="role"' in html
    assert "advance" in html and "repayment" in html
    assert f"/financials/transactions/{out.id}/link-debt" in html and f"/financials/transactions/{tr.id}/link-debt" in html
    # the transfer-type candidate must make the user choose a role
    tr_block = html[html.index(f"/transactions/{tr.id}/link-debt"):]
    assert "choose" in tr_block.split("</form>")[0].lower()


def test_link_debt_errors_are_plain_text(client, session):
    t = _mk(session, "TRF MBWAY P/ TEST PERSON", 600.0, TransactionType.TRANSFER)
    r = client.post(f"/financials/transactions/{t.id}/link-debt", data={"person_name": "Test Person"})
    assert r.status_code == 400 and r.headers["content-type"].startswith("text/plain")
    assert r.text == "Choose the direction: a transfer does not say whether you lent or borrowed"
    from app.services.debt_ledger import create_informal_debt
    debt = create_informal_debt(session, "Test Person", DebtDirection.OWED_TO_US, opening_amount=10.0)
    session.commit()
    r = client.post(f"/financials/transactions/{t.id}/link-debt", data={"existing_debt_id": str(debt.id)})
    assert r.status_code == 400 and r.headers["content-type"].startswith("text/plain")
    assert r.text == "Choose a role (advance or repayment): a transfer has no reliable direction"


def test_needs_review_page_has_error_handler_and_required_role_for_transfers(client, session):
    _mk(session, "TRF MBWAY P/ TEST OTHER", 700.0, TransactionType.TRANSFER)
    _mk(session, "TRF SEPA+ P/ TEST PERSON", 900.0, TransactionType.DEBIT)
    html = client.get("/financials/transactions/needs-review").text
    assert "htmx:responseError" in html and "textContent" in html and 'id="needs-review-error"' in html
    selects = [h.split("</select>")[0] for h in html.split('<select name="role"')[1:]]
    assert len(selects) == 2
    transfer = next(x for x in selects if "required" in x[:40])
    assert 'value=""' not in transfer and "advance" in transfer and "repayment" in transfer
    assert sum("required" in x[:40] for x in selects) == 1  # only the TRANSFER-type one
    assert "choose advance or repayment" in html.lower()


def test_needs_review_title_has_no_script_and_handler_is_in_the_content(client, session):
    html = client.get("/financials/transactions/needs-review").text
    title = html[html.index("<title>"):html.index("</title>")]
    assert "<script" not in title and "Needs Review" in title
    assert html.count("htmx:responseError") == 1


def test_new_debt_role_must_agree_with_the_direction(client, session):
    out = _mk(session, "TRF SEPA+ P/ TEST A", 700.0, TransactionType.DEBIT)
    r = client.post(f"/financials/transactions/{out.id}/link-debt",
                    data={"person_name": "Test A", "direction": "owed_by_us", "role": "advance"})
    assert r.status_code == 400 and r.headers["content-type"].startswith("text/plain")  # a debit cannot be an advance on a loan we received
    inn = _mk(session, "TRF SEPA+ P/ TEST B", 800.0, TransactionType.CREDIT)
    r = client.post(f"/financials/transactions/{inn.id}/link-debt",
                    data={"person_name": "Test B", "direction": "owed_to_us", "role": "advance"})
    assert r.status_code == 400  # a credit cannot be an advance on a loan we gave
    assert session.exec(select(Debt)).all() == []
    # consistent combinations work; a missing role means advance
    ok = client.post(f"/financials/transactions/{out.id}/link-debt",
                     data={"person_name": "Test A", "direction": "owed_to_us", "role": "advance"})
    assert ok.status_code == 200


def test_new_debt_inconsistent_direction_without_role_is_refused_too(client, session):
    inn = _mk(session, "TRF SEPA+ P/ TEST B", 800.0, TransactionType.CREDIT)
    r = client.post(f"/financials/transactions/{inn.id}/link-debt", data={"person_name": "Test B", "direction": "owed_to_us"})
    assert r.status_code == 400


def test_new_debt_from_a_transfer_honours_the_explicit_role(client, session):
    t = _mk(session, "TRF MBWAY P/ TEST C", 90.0, TransactionType.TRANSFER)
    r = client.post(f"/financials/transactions/{t.id}/link-debt",
                    data={"person_name": "Test C", "direction": "owed_to_us", "role": "repayment"})
    assert r.status_code == 200
    assert _entries(session)[0].kind == "repayment"


def test_person_name_over_80_characters_is_refused(client, session):
    t = _mk(session, "TRF SEPA+ P/ TEST D", 700.0, TransactionType.DEBIT)
    r = client.post(f"/financials/transactions/{t.id}/link-debt", data={"person_name": "x" * 81, "direction": "owed_to_us"})
    assert r.status_code == 400 and "80" in r.text
    assert session.exec(select(Debt)).all() == []


# --- Needs Review scale: pagination and lazily loaded category options -------

def _bulk_review_data(session, n=1500):
    """n unconfirmed merchants (one transaction each) and n unsorted groups, built
    through the normal constructors. Merchant 0 and group 0 have the most transactions."""
    from app.models.merchant import Merchant
    from app.services.taxonomy import ensure_taxonomy, get_node
    ensure_taxonomy(session)
    filed = get_node(session, "food.groceries.supermarket")
    doc = Document(filename="bulk.pdf", file_path="/tmp/bulk.pdf", content_hash="h-bulk", source=DocumentSource.MANUAL)
    session.add(doc)
    session.commit()
    session.refresh(doc)
    unconfirmed, groups = [], []
    for i in range(n):
        m = Merchant(canonical_name=f"Synth New {i:04d}", normalized_key=f"synth-new-{i:04d}")
        g = Merchant(canonical_name=f"Synth Group {i:04d}", normalized_key=f"synth-group-{i:04d}", confirmed=True)
        session.add(m)
        session.add(g)
        unconfirmed.append(m)
        groups.append(g)
    session.commit()
    for i, (m, g) in enumerate(zip(unconfirmed, groups)):
        copies = 3 if i == n - 1 else 1  # the last one is the busiest: it must come first
        for _ in range(copies):
            session.add(Transaction(document_id=doc.id, provider=f"SYNTH NEW {i}", category=Category.OTHER,
                                    amount=1.0, merchant_id=m.id, category_id=filed.id))  # filed: only the groups are unsorted
            session.add(Transaction(document_id=doc.id, provider=f"SYNTH GROUP {i}", category=Category.OTHER,
                                    amount=1.0, merchant_id=g.id))
    session.commit()
    return unconfirmed, groups


def test_needs_review_is_bounded_in_size_and_rows(client, session):
    _bulk_review_data(session)
    response = client.get("/financials/transactions/needs-review")
    assert response.status_code == 200
    assert len(response.content) < 400_000
    html = response.text
    assert html.count("Save &amp; Confirm") == 50
    assert html.count(">File all<") == 50
    assert "Showing 50 of 1,500" in html
    assert html.count("Show next 50") >= 2
    assert html.index("Synth New 1499") < html.index("Synth New 0000")  # busiest first, then by name
    assert "Synth New 0049" not in html and "Synth New 0050" not in html


def test_needs_review_offsets_return_the_next_page(client, session):
    _bulk_review_data(session)
    r = client.get("/financials/transactions/needs-review", params={"m_off": 50, "u_off": 1450})
    html = r.text
    assert "Showing 51–100 of 1,500" in html
    assert "Showing 1,451–1,500 of 1,500" in html
    assert html.count("Save &amp; Confirm") == 50
    assert html.count(">File all<") == 50
    assert "Synth New 1499" not in html  # that was on page one
    rows = client.get("/financials/transactions/needs-review/rows", params={"m_off": 1450})
    assert rows.status_code == 200 and "<html" not in rows.text and "Showing 1,451–1,500 of 1,500" in rows.text


def test_node_options_are_identical_cacheable_and_escaped(client, session):
    from app.services.taxonomy import ensure_taxonomy
    ensure_taxonomy(session)
    r = client.get("/financials/transactions/node-options")
    assert r.status_code == 200 and "<html" not in r.text
    assert r.headers["cache-control"] == "private, max-age=600"
    assert client.get("/financials/transactions/node-options", params={"selected": "food.groceries.supermarket"}).text == r.text
    assert client.get("/financials/transactions/node-options", params={"selected": '"><script>x</script>'}).text == r.text
    assert '<optgroup label="Food">' in r.text
    assert 'value="food.groceries.supermarket"' in r.text
    assert "Food \u203a Groceries \u203a Supermarket" in r.text
    assert 'value="food"' not in r.text and "unsorted" not in r.text and "<script>" not in r.text


def test_needs_review_rows_load_options_lazily(client, session):
    from app.models.merchant import Merchant
    from app.services.taxonomy import ensure_taxonomy, get_node
    ensure_taxonomy(session)
    node = get_node(session, "food.groceries.supermarket")
    session.add(Merchant(canonical_name="Lazy Co", normalized_key="lazy-co", default_category_id=node.id))
    session.commit()
    html = client.get("/financials/transactions/needs-review").text
    assert html.count("<option") < 40
    assert 'hx-get="/financials/transactions/node-options"' in html
    assert 'hx-trigger="intersect once, focus once"' in html
    assert 'data-selected="food.groceries.supermarket"' in html
    assert 'hx-on::after-swap="this.value=this.dataset.selected"' in html
    assert 'hx-params="none"' in html
    assert 'value="food.groceries.supermarket" selected' in html


def test_confirm_with_lazily_loaded_option_keeps_the_offset(client, session):
    unconfirmed, _ = _bulk_review_data(session, n=120)
    target = unconfirmed[60]  # on page two (offset 50) once the busiest is first
    options = client.get("/financials/transactions/node-options").text
    assert 'value="food.groceries.supermarket"' in options
    r = client.post(f"/financials/transactions/merchants/{target.id}/confirm",
                    data={"category_node": "food.groceries.supermarket", "m_off": "50", "u_off": "0"})
    assert r.status_code == 200
    session.refresh(target)
    assert target.confirmed is True
    assert "Showing 51–100 of 119" in r.text
    assert "Showing 50 of 120" in r.text  # the untouched unsorted section keeps its place
    assert 'hx-vals=\'{"m_off": 50' in r.text


def test_offset_past_the_end_falls_back_to_the_last_page(client, session):
    _bulk_review_data(session, n=60)
    r = client.get("/financials/transactions/needs-review", params={"m_off": 500})
    assert "Showing 51–60 of 60" in r.text


def test_debt_candidates_are_limited_to_50(client, session):
    for i in range(55):
        _make_transaction(session, f"TRF P/ PESSOA {chr(65 + i % 26)}{chr(65 + i // 26)}", Category.TRANSFER, 600.0)
    html = client.get("/financials/transactions/needs-review").text
    assert html.count(">Link Debt<") == 50
    assert "Showing 50 of 55" in html
