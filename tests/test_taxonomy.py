from sqlmodel import select

from app.models.category_node import CategoryNode
from app.services.taxonomy import (
    UNSORTED_SLUG, descendant_ids, ensure_taxonomy, get_node, leaf_slugs, path_label,
)


def test_ensure_taxonomy_builds_three_levels(session):
    created = ensure_taxonomy(session)
    assert created > 100
    assert {n.level for n in session.exec(select(CategoryNode)).all()} == {1, 2, 3}


def test_ensure_taxonomy_is_idempotent(session):
    ensure_taxonomy(session)
    assert ensure_taxonomy(session) == 0


def test_path_label_and_kind(session):
    ensure_taxonomy(session)
    node = get_node(session, "food.groceries.supermarket")
    assert path_label(session, node) == "Food › Groceries › Supermarket"
    assert node.kind == "out" and node.cadence == "monthly"


def test_yearly_and_loan_cadences(session):
    ensure_taxonomy(session)
    assert get_node(session, "housing.property-taxes-insurance.imi-property-tax").cadence == "yearly"
    assert get_node(session, "loans-debt.loan-repayments.car-loan").cadence == "loan"
    assert get_node(session, "income.psi.sessions").kind == "in"
    assert get_node(session, "internal-transfers.between-my-accounts.top-ups-card-payments").kind == "neutral"


def test_loan_insurance_nodes_are_monthly_spend_with_the_insurance_legacy_category(session):
    from app.models.transaction import Category
    from app.services.taxonomy import legacy_category_for
    ensure_taxonomy(session)
    parent = get_node(session, "loans-debt.loan-insurance")
    assert parent.cadence == "monthly" and parent.kind == "out"
    for slug, name in (("loans-debt.loan-insurance.life-insurance-loan", "Life insurance (loan)"),
                       ("loans-debt.loan-insurance.building-insurance-loan", "Building insurance (loan)")):
        node = get_node(session, slug)
        assert node.name == name and node.cadence == "monthly" and node.parent_id == parent.id
        assert legacy_category_for(session, node) == Category.INSURANCE


def test_descendants_include_self(session):
    ensure_taxonomy(session)
    food = get_node(session, "food")
    ids = descendant_ids(session, food.id)
    assert food.id in ids and get_node(session, "food.groceries.supermarket").id in ids
    assert get_node(session, "housing").id not in ids


def test_leaf_slugs_are_only_level_3_and_include_unsorted(session):
    ensure_taxonomy(session)
    slugs = leaf_slugs(session)
    assert UNSORTED_SLUG in slugs
    assert all(s.count(".") == 2 for s in slugs)


from app.models.document import Document, DocumentSource
from app.models.transaction import Transaction


def test_transaction_and_merchant_accept_node_ids(session):
    from app.models.merchant import Merchant
    ensure_taxonomy(session)
    node = get_node(session, "food.groceries.supermarket")
    doc = Document(filename="a.pdf", file_path="/tmp/a.pdf", content_hash="h1", source=DocumentSource.MANUAL)
    session.add(doc); session.commit()
    t = Transaction(document_id=doc.id, provider="PINGO DOCE", amount=10.0, category_id=node.id)
    m = Merchant(canonical_name="Pingo Doce", normalized_key="pingo doce", default_category_id=node.id)
    session.add_all([t, m]); session.commit()
    assert session.get(Transaction, t.id).category_id == node.id
    assert session.get(Merchant, m.id).default_category_id == node.id


def test_savings_groups_seeded(session):
    import pytest
    from app.services.budget_service import set_budget

    ensure_taxonomy(session)
    out = get_node(session, "savings-investments.contributions.fund-subscriptions")
    assert out.kind == "out" and out.cadence == "monthly"
    inn = get_node(session, "savings-investments-in.withdrawals.fund-redemptions")
    assert inn.kind == "in"
    assert set_budget(session, out.id, 2026, 100.0).amount == 100.0
    with pytest.raises(ValueError):
        set_budget(session, inn.id, 2026, 100.0)
