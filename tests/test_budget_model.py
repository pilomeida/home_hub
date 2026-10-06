import pytest

from app.services.budget_service import get_budgets, set_budget
from app.services.taxonomy import ensure_taxonomy, get_node


def test_set_budget_upserts_one_row_per_node_and_year(session):
    ensure_taxonomy(session)
    node = get_node(session, "food.groceries.supermarket")
    set_budget(session, node.id, 2026, 500.0)
    set_budget(session, node.id, 2026, 550.0)
    budgets = get_budgets(session, 2026)
    assert list(budgets) == [node.id] and budgets[node.id].amount == 550.0
    assert get_budgets(session, 2027) == {}


def test_zero_amount_removes_the_budget(session):
    ensure_taxonomy(session)
    node = get_node(session, "food.groceries.supermarket")
    set_budget(session, node.id, 2026, 500.0)
    set_budget(session, node.id, 2026, 0)
    assert get_budgets(session, 2026) == {}


def test_yearly_budget_keeps_expected_month(session):
    ensure_taxonomy(session)
    node = get_node(session, "housing.property-taxes-insurance.imi-property-tax")
    b = set_budget(session, node.id, 2026, 420.0, expected_month=4)
    assert b.expected_month == 4


@pytest.mark.parametrize("slug", [
    "food",                                                    # group, not a sub-category
    "income.psi.sessions",                                     # inflow
    "loans-debt.loan-repayments.car-loan",                     # loan cadence
    "internal-transfers.between-my-accounts.santander-card",   # neutral
])
def test_budget_rejected_on_non_budgetable_nodes(session, slug):
    ensure_taxonomy(session)
    with pytest.raises(ValueError):
        set_budget(session, get_node(session, slug).id, 2026, 100.0)


def test_expected_month_rejected_for_monthly_nodes_and_bad_values(session):
    ensure_taxonomy(session)
    monthly = get_node(session, "food.groceries.supermarket")
    yearly = get_node(session, "housing.property-taxes-insurance.imi-property-tax")
    with pytest.raises(ValueError):
        set_budget(session, monthly.id, 2026, 100.0, expected_month=3)
    with pytest.raises(ValueError):
        set_budget(session, yearly.id, 2026, 100.0, expected_month=13)


def test_zero_keeps_a_tombstone_only_when_an_earlier_year_budget_exists(session):
    from app.models.budget import Budget
    ensure_taxonomy(session)
    node = get_node(session, "food.groceries.supermarket")
    session.add(Budget(node_id=node.id, year=2025, amount=300.0))
    session.commit()
    assert set_budget(session, node.id, 2026, 0) is None
    assert get_budgets(session, 2026) == {}  # tombstone is not a budget
    from sqlmodel import select
    row = session.exec(select(Budget).where(Budget.node_id == node.id, Budget.year == 2026)).one()
    assert row.amount == 0.0
