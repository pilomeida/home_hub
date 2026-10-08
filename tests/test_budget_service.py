from datetime import date

from app.models.document import Document, DocumentSource
from app.models.transaction import Transaction, TransactionType
from app.models.budget import Budget
from app.services.budget_service import get_budget_overview, set_budget
from app.services.taxonomy import UNSORTED_SLUG, ensure_taxonomy, file_transaction, get_node

TODAY = date(2026, 10, 15)
SUPERMARKET = "food.groceries.supermarket"
MARKETS = "food.groceries.markets"
RESTAURANTS = "food.eat-out.restaurants"
IMI = "taxes-financial-costs.taxes.imi-property-tax"
PSI = "income.psi.sessions"


def _doc(session):
    d = session.exec(__import__("sqlmodel").select(Document)).first()
    if d:
        return d
    d = Document(filename="d.pdf", file_path="/tmp/d.pdf", content_hash="h-d", source=DocumentSource.MANUAL)
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def _txn(session, slug, amount, ttype, paid):
    """Create a transaction and file it through the production path."""
    t = Transaction(document_id=_doc(session).id, provider="X", transaction_type=ttype,
                    amount=amount, currency="EUR", paid_date=paid)
    session.add(t)
    session.flush()
    file_transaction(session, t, get_node(session, slug))
    session.commit()
    return t


def _debit(session, slug, amount, paid):
    return _txn(session, slug, amount, TransactionType.DEBIT, paid)


def _lines(overview):
    return {l.name: l for g in overview.groups for c in g.categories for l in c.lines}


def test_spend_per_subcategory_sums_ignores_transfers_and_credits_reduce(session):
    ensure_taxonomy(session)
    for d in (2, 5, 9):
        _debit(session, SUPERMARKET, 50.0, date(2026, 10, d))
    # TRANSFER rows filed under neutral or Unsorted nodes are ignored
    _txn(session, "internal-transfers.between-my-accounts.top-ups-card-payments", 999.0,
         TransactionType.TRANSFER, date(2026, 10, 6))
    _txn(session, UNSORTED_SLUG, 888.0, TransactionType.TRANSFER, date(2026, 10, 6))
    assert _lines(get_budget_overview(session, TODAY))["Supermarket"].spent == 150.0
    _txn(session, SUPERMARKET, 20.0, TransactionType.CREDIT, date(2026, 10, 7))
    assert _lines(get_budget_overview(session, TODAY))["Supermarket"].spent == 130.0


def test_rollups_sum_and_worst_status_wins(session):
    ensure_taxonomy(session)
    set_budget(session, get_node(session, SUPERMARKET).id, 2026, 100.0)
    set_budget(session, get_node(session, MARKETS).id, 2026, 100.0)
    _debit(session, SUPERMARKET, 150.0, date(2026, 10, 3))
    _debit(session, MARKETS, 10.0, date(2026, 10, 4))
    _debit(session, RESTAURANTS, 40.0, date(2026, 10, 5))
    ov = get_budget_overview(session, TODAY)
    lines = _lines(ov)
    assert lines["Supermarket"].status == "over"
    assert lines["Markets"].status == "ok"
    food = next(g for g in ov.groups if g.name == "Food")
    groceries = next(c for c in food.categories if c.name == "Groceries")
    assert groceries.status == "over" and groceries.spent == 160.0 and groceries.budget == 200.0
    assert groceries.expected == sum(l.expected for l in groceries.lines)
    assert food.status == "over" and food.spent == 200.0 and food.budget == 200.0
    dining = next(c for c in food.categories if c.name == "Eat out")
    assert dining.status == "no_budget" and dining.budget == 0.0


def test_no_budget_line_uses_history_and_idle_node_is_hidden(session):
    ensure_taxonomy(session)
    _debit(session, RESTAURANTS, 120.0, date(2026, 9, 10))
    _debit(session, RESTAURANTS, 120.0, date(2026, 8, 10))
    ov = get_budget_overview(session, TODAY)
    lines = _lines(ov)
    line = lines["Restaurants"]
    assert line.status == "no_budget" and line.budget is None
    assert line.spent == 0.0 and abs(line.expected - 40.0) < 1e-9
    assert "Markets" not in lines and "Electricity" not in lines


def test_unsorted_debit_goes_to_unsorted_spent_only(session):
    ensure_taxonomy(session)
    _debit(session, UNSORTED_SLUG, 33.0, date(2026, 10, 2))
    _txn(session, UNSORTED_SLUG, 500.0, TransactionType.CREDIT, date(2026, 10, 3))
    ov = get_budget_overview(session, TODAY)
    assert ov.unsorted_spent == 33.0
    assert _lines(ov) == {} and ov.spend_spent == 0.0
    assert ov.income.expected == 0.0


def test_yearly_line_visibility_and_expected(session):
    ensure_taxonomy(session)
    set_budget(session, get_node(session, IMI).id, 2026, 420.0, expected_month=10)
    line = _lines(get_budget_overview(session, TODAY))["IMI property tax"]
    assert line.expected == 420.0 and line.spent == 0.0
    # November, still unpaid: overdue, still counted.
    assert _lines(get_budget_overview(session, date(2026, 11, 15)))["IMI property tax"].expected == 420.0
    # Not due yet: hidden.
    assert "IMI property tax" not in _lines(get_budget_overview(session, date(2026, 3, 15)))
    # Paid in October: shown in October, hidden in November.
    _debit(session, IMI, 420.0, date(2026, 10, 8))
    assert _lines(get_budget_overview(session, TODAY))["IMI property tax"].spent == 420.0
    assert "IMI property tax" not in _lines(get_budget_overview(session, date(2026, 11, 15)))


def test_income_month_view_uses_seasonality(session):
    ensure_taxonomy(session)
    _txn(session, PSI, 1000.0, TransactionType.CREDIT, date(2025, 10, 10))
    _txn(session, PSI, 1400.0, TransactionType.CREDIT, date(2024, 10, 10))
    ov = get_budget_overview(session, TODAY)
    assert (ov.income.low, ov.income.expected, ov.income.high) == (1000.0, 1200.0, 1400.0)
    assert ov.income.low <= ov.income.expected <= ov.income.high
    assert ov.income_received == 0.0
    _txn(session, PSI, 1100.0, TransactionType.CREDIT, date(2026, 10, 2))
    assert get_budget_overview(session, TODAY).income_received == 1100.0


def test_year_view_totals_and_net(session):
    ensure_taxonomy(session)
    set_budget(session, get_node(session, SUPERMARKET).id, 2026, 100.0)
    set_budget(session, get_node(session, IMI).id, 2026, 420.0, expected_month=10)
    for m in (7, 8, 9, 10):
        _debit(session, SUPERMARKET, 80.0, date(2026, m, 5))
    _debit(session, RESTAURANTS, 60.0, date(2026, 9, 5))
    _txn(session, PSI, 1000.0, TransactionType.CREDIT, date(2025, 11, 10))
    _txn(session, PSI, 2000.0, TransactionType.CREDIT, date(2026, 5, 10))
    ov = get_budget_overview(session, TODAY, view="year")
    lines = _lines(ov)
    assert ov.view == "year"
    assert lines["Supermarket"].spent == 320.0
    assert lines["IMI property tax"].expected == 420.0
    assert ov.spend_expected == sum(l.expected for l in lines.values())
    assert ov.net.expected == ov.income.expected - ov.spend_expected
    assert ov.net.low == ov.income.low - ov.spend_expected
    assert ov.net.high == ov.income.high - ov.spend_expected
    assert ov.income_received == 2000.0


ELECTRICITY = "housing.utilities.electricity"


def _housing(ov):
    return next(g for g in ov.groups if g.name == "Housing")


def _taxes(ov):
    return next(g for g in ov.groups if g.name == "Taxes & financial costs")


def test_group_budget_excludes_yearly_line_not_due_this_month(session):
    ensure_taxonomy(session)
    set_budget(session, get_node(session, ELECTRICITY).id, 2026, 100.0)
    set_budget(session, get_node(session, IMI).id, 2026, 420.0, expected_month=12)
    ov = get_budget_overview(session, TODAY)
    assert _housing(ov).budget == 100.0
    assert not any(g.name == "Taxes & financial costs" for g in ov.groups)  # IMI is due in December, not shown in October
    assert _lines(ov)["Electricity"].budget_in_period == 100.0


def test_group_budget_includes_remaining_yearly_amount_when_due(session):
    ensure_taxonomy(session)
    set_budget(session, get_node(session, ELECTRICITY).id, 2026, 100.0)
    set_budget(session, get_node(session, IMI).id, 2026, 420.0, expected_month=10)
    ov = get_budget_overview(session, TODAY)
    imi = _lines(ov)["IMI property tax"]
    assert imi.budget == 420.0 and imi.budget_in_period == 420.0
    assert _housing(ov).budget == 100.0 and _taxes(ov).budget == 420.0
    year = get_budget_overview(session, TODAY, view="year")
    assert _housing(year).budget == 100.0 * 12 and _taxes(year).budget == 420.0


def test_unsorted_received_exposed_month_and_year(session):
    ensure_taxonomy(session)
    _txn(session, UNSORTED_SLUG, 500.0, TransactionType.CREDIT, date(2026, 10, 3))
    _txn(session, UNSORTED_SLUG, 70.0, TransactionType.CREDIT, date(2026, 5, 3))
    _txn(session, UNSORTED_SLUG, 33.0, TransactionType.TRANSFER, date(2026, 10, 3))
    assert get_budget_overview(session, TODAY).unsorted_received == 500.0
    assert get_budget_overview(session, TODAY, view="year").unsorted_received == 570.0


def test_transfer_filed_under_out_node_counts_as_spend(session):
    ensure_taxonomy(session)
    repairs = "housing.home-running-costs.repairs-maintenance"
    _debit(session, repairs, 100.0, date(2026, 10, 2))
    _txn(session, repairs, 40.0, TransactionType.TRANSFER, date(2026, 10, 3))
    assert _lines(get_budget_overview(session, TODAY))["Repairs & maintenance"].spent == 140.0


def test_transfer_filed_under_in_node_counts_as_income(session):
    ensure_taxonomy(session)
    _txn(session, PSI, 250.0, TransactionType.TRANSFER, date(2026, 10, 3))
    assert get_budget_overview(session, TODAY).income_received == 250.0


def test_income_history_is_zero_filled_for_seasonal_gaps(session):
    ensure_taxonomy(session)
    _txn(session, PSI, 300.0, TransactionType.CREDIT, date(2024, 8, 10))
    _txn(session, PSI, 500.0, TransactionType.CREDIT, date(2025, 3, 10))
    ov = get_budget_overview(session, date(2026, 8, 15))
    assert (ov.income.low, ov.income.expected, ov.income.high) == (0.0, 150.0, 300.0)


def test_income_source_without_data_stays_out(session):
    ensure_taxonomy(session)
    _txn(session, PSI, 300.0, TransactionType.CREDIT, date(2025, 10, 10))
    ov = get_budget_overview(session, TODAY)
    assert ov.income.expected == 300.0  # only Psi; Salary etc. contribute nothing


def test_only_recurring_income_is_forecast(session):
    ensure_taxonomy(session)
    _txn(session, "income.gifts-other.gifts-received", 5000.0, TransactionType.CREDIT, date(2025, 10, 10))
    _txn(session, "savings-investments.withdrawals.fund-investment-redemptions", 800.0, TransactionType.CREDIT,
         date(2025, 10, 11))
    _txn(session, PSI, 1000.0, TransactionType.CREDIT, date(2025, 10, 12))
    ov = get_budget_overview(session, TODAY)
    assert (ov.income.low, ov.income.expected, ov.income.high) == (1000.0, 1000.0, 1000.0)
    # what has actually been received still counts, in both views, without projection
    _txn(session, "income.gifts-other.gifts-received", 200.0, TransactionType.CREDIT, date(2026, 10, 2))
    month = get_budget_overview(session, TODAY)
    assert month.income.expected == 1200.0 and month.income_received == 200.0
    year = get_budget_overview(session, TODAY, view="year")
    assert year.income_received == 200.0
    assert year.income.low <= year.income.expected <= year.income.high


def test_budget_carries_over_from_earlier_year_and_current_row_overrides(session):
    ensure_taxonomy(session)
    node = get_node(session, SUPERMARKET)
    session.add(Budget(node_id=node.id, year=2025, amount=300.0))
    session.add(Budget(node_id=node.id, year=2024, amount=111.0))
    session.commit()
    ov = get_budget_overview(session, TODAY)
    line = _lines(ov)["Supermarket"]
    assert line.budget == 300.0 and line.inherited is True
    assert ov.has_budgets is True
    set_budget(session, node.id, 2026, 400.0)
    line = _lines(get_budget_overview(session, TODAY))["Supermarket"]
    assert line.budget == 400.0 and line.inherited is False


def test_carried_over_yearly_budget_keeps_expected_month(session):
    ensure_taxonomy(session)
    node = get_node(session, IMI)
    session.add(Budget(node_id=node.id, year=2025, amount=420.0, expected_month=10))
    session.commit()
    line = _lines(get_budget_overview(session, TODAY))["IMI property tax"]
    assert line.expected == 420.0 and line.expected_month == 10 and line.inherited


def test_has_budgets_true_even_when_all_budgeted_lines_are_hidden(session):
    ensure_taxonomy(session)
    set_budget(session, get_node(session, IMI).id, 2026, 420.0, expected_month=12)
    ov = get_budget_overview(session, TODAY)
    assert _lines(ov) == {} and ov.has_budgets is True


def test_zero_in_current_year_clears_a_carried_over_budget(session):
    ensure_taxonomy(session)
    node = get_node(session, SUPERMARKET)
    session.add(Budget(node_id=node.id, year=2025, amount=300.0))
    session.commit()
    set_budget(session, node.id, 2026, 0)
    ov = get_budget_overview(session, TODAY)
    assert _lines(ov) == {}  # no spend, no budget: hidden
    assert ov.has_budgets is False
    _debit(session, SUPERMARKET, 20.0, date(2026, 10, 3))
    line = _lines(get_budget_overview(session, TODAY))["Supermarket"]
    assert line.budget is None and line.inherited is False
    assert line.status == "no_budget" and line.budget_in_period == 0.0
    set_budget(session, node.id, 2026, 250.0)
    line = _lines(get_budget_overview(session, TODAY))["Supermarket"]
    assert line.budget == 250.0 and line.inherited is False


# ---- Task 7: loan instalments in Net (synthetic loans, production linking path) ----

def _loan_with_snapshot(session, number="9000001", status="active", next_due=date(2026, 10, 20),
                        instalment=500.0, remaining=2000.0, rate=3.0, as_of=date(2026, 10, 1)):
    from decimal import Decimal
    from app.models.debt import Debt, DebtDirection, DebtKind
    from app.models.position import LoanSnapshot
    debt = Debt(kind=DebtKind.FORMAL, direction=DebtDirection.OWED_BY_US, original_amount=100000.0,
                current_balance=Decimal("0"), name=f"Loan {number}", external_number=number,
                status=status, loan_type="mortgage")
    session.add(debt)
    session.commit()
    session.refresh(debt)
    session.add(LoanSnapshot(debt_id=debt.id, as_of=as_of, capital_remaining=remaining,
                             rate_percent=rate, next_due_date=next_due, next_instalment=instalment,
                             document_id=_doc(session).id))
    session.commit()
    return debt


def _pay_instalment(session, debt, amount, paid):
    from app.services.loan_linking import link_transaction_to_loan
    t = Transaction(document_id=_doc(session).id, provider=f"COB.REC.31.{debt.external_number}/ 1",
                    transaction_type=TransactionType.DEBIT, amount=amount, currency="EUR", paid_date=paid)
    session.add(t)
    session.flush()
    assert link_transaction_to_loan(session, t) is True
    session.commit()
    return t


def test_no_loans_means_no_instalments(session):
    ensure_taxonomy(session)
    ov = get_budget_overview(session, TODAY, "month")
    assert ov.loan_instalments == 0.0 and ov.loan_instalments_paid == 0.0


def test_unpaid_loan_due_this_month_lowers_net(session):
    ensure_taxonomy(session)
    _loan_with_snapshot(session)
    base = get_budget_overview(session, TODAY, "month")
    assert base.loan_instalments == 500.0 and base.loan_instalments_paid == 0.0
    assert base.net.expected == base.income.expected - base.spend_expected - 500.0


def test_paid_instalment_counts_once_and_not_as_spend(session):
    ensure_taxonomy(session)
    debt = _loan_with_snapshot(session)
    before = get_budget_overview(session, TODAY, "month")
    _pay_instalment(session, debt, 500.0, date(2026, 10, 5))
    ov = get_budget_overview(session, TODAY, "month")
    assert ov.loan_instalments == 0.0 and ov.loan_instalments_paid == 500.0
    assert ov.spend_spent == before.spend_spent
    assert ov.spend_expected == before.spend_expected
    assert ov.net.expected == ov.income.expected - ov.spend_expected - 500.0
    assert ov.unsorted_spent == 0.0


def test_loan_due_next_month_and_closed_loans_contribute_nothing(session):
    ensure_taxonomy(session)
    _loan_with_snapshot(session, "9000001", next_due=date(2026, 11, 3))
    _loan_with_snapshot(session, "9000002", status="closed")
    ov = get_budget_overview(session, TODAY, "month")
    assert ov.loan_instalments == 0.0 and ov.loan_instalments_paid == 0.0


def test_year_view_projects_remaining_months_and_stops_at_payoff(session):
    ensure_taxonomy(session)
    # 0% rate, 900 left at 500/month -> two instalments left (Oct, Nov)
    _loan_with_snapshot(session, "9000001", remaining=900.0, rate=0.0)
    # long loan: Oct, Nov, Dec all projected
    _loan_with_snapshot(session, "9000002", remaining=50000.0, next_due=date(2026, 10, 20), instalment=200.0)
    ov = get_budget_overview(session, TODAY, "year")
    assert ov.loan_instalments == 2 * 500.0 + 3 * 200.0
    assert ov.loan_instalments_paid == 0.0


def test_year_view_paid_ytd_and_remaining_after_this_months_payment(session):
    ensure_taxonomy(session)
    debt = _loan_with_snapshot(session, remaining=50000.0)
    _pay_instalment(session, debt, 500.0, date(2026, 3, 5))
    _pay_instalment(session, debt, 500.0, date(2026, 10, 5))
    ov = get_budget_overview(session, TODAY, "year")
    assert ov.loan_instalments_paid == 1000.0
    assert ov.loan_instalments == 2 * 500.0  # Nov, Dec (October already paid)


def test_informal_debts_do_not_count_as_loan_instalments(session):
    from decimal import Decimal
    from app.models.debt import Debt, DebtDirection, DebtKind
    ensure_taxonomy(session)
    d = Debt(kind=DebtKind.INFORMAL, direction=DebtDirection.OWED_BY_US, original_amount=300.0,
             current_balance=Decimal("300"))
    session.add(d)
    session.commit()
    session.refresh(d)
    t = _debit(session, SUPERMARKET, 50.0, date(2026, 10, 3))
    t.debt_id = d.id
    session.add(t)
    session.commit()
    ov = get_budget_overview(session, TODAY, "month")
    assert ov.loan_instalments == 0.0 and ov.loan_instalments_paid == 0.0


# ---- I3: stale statements still expect this month's instalment ----------------


def test_stale_snapshot_rolls_next_due_forward_into_this_month(session):
    ensure_taxonomy(session)
    # statement from 5 months ago said "next due 20 Jun"; nothing newer has arrived
    _loan_with_snapshot(session, remaining=50000.0, next_due=date(2026, 6, 20), as_of=date(2026, 5, 31))
    ov = get_budget_overview(session, TODAY, "month")
    assert ov.loan_instalments == 500.0 and ov.loan_instalments_paid == 0.0
    assert ov.net.expected == ov.income.expected - ov.spend_expected - 500.0


def test_stale_snapshot_due_day_later_in_month_and_day_clamping(session):
    ensure_taxonomy(session)
    _loan_with_snapshot(session, "9000001", remaining=50000.0, next_due=date(2026, 6, 28), as_of=date(2026, 5, 31))
    _loan_with_snapshot(session, "9000002", remaining=50000.0, next_due=date(2026, 6, 30), as_of=date(2026, 5, 31))
    assert get_budget_overview(session, TODAY, "month").loan_instalments == 1000.0


def test_stale_snapshot_already_paid_this_month_is_paid_not_expected(session):
    ensure_taxonomy(session)
    debt = _loan_with_snapshot(session, remaining=50000.0, next_due=date(2026, 6, 20), as_of=date(2026, 5, 31))
    _pay_instalment(session, debt, 500.0, date(2026, 10, 5))
    ov = get_budget_overview(session, TODAY, "month")
    assert ov.loan_instalments == 0.0 and ov.loan_instalments_paid == 500.0


def test_loan_with_nothing_left_expects_nothing(session):
    ensure_taxonomy(session)
    _loan_with_snapshot(session, remaining=0.0, next_due=date(2026, 6, 20), as_of=date(2026, 5, 31))
    assert get_budget_overview(session, TODAY, "month").loan_instalments == 0.0
    assert get_budget_overview(session, TODAY, "year").loan_instalments == 0.0


def test_stale_snapshot_year_view_projects_from_rolled_date_to_december(session):
    ensure_taxonomy(session)
    _loan_with_snapshot(session, remaining=50000.0, instalment=200.0, next_due=date(2026, 7, 20),
                        as_of=date(2026, 6, 30))
    assert get_budget_overview(session, TODAY, "year").loan_instalments == 3 * 200.0  # Oct, Nov, Dec
