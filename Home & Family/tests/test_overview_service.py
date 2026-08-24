from datetime import date
from decimal import Decimal

from app.models.account import Account, AccountType
from app.models.commitment import Cadence, Commitment
from app.models.debt import Debt, DebtDirection, DebtKind
from app.models.document import Document, DocumentSource
from app.models.transaction import Category, Transaction, TransactionType
from app.services.overview_service import get_flow_kpis, _monthly_flow_totals, get_cash_kpi, get_debt_kpi, get_yearly_commitments_card, get_category_comparison


def _doc(session, name="doc"):
    document = Document(
        filename=f"{name}.pdf", file_path=f"/tmp/{name}.pdf", content_hash=f"h-{name}",
        source=DocumentSource.MANUAL,
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    return document


def _txn(session, document, provider, amount, ttype, paid_date, category=Category.OTHER_EXPENSE):
    t = Transaction(
        document_id=document.id, provider=provider, category=category,
        transaction_type=ttype, amount=amount, currency="EUR", paid_date=paid_date,
    )
    session.add(t)
    session.commit()
    return t


def test_monthly_flow_totals_buckets_by_calendar_month(session):
    document = _doc(session)
    _txn(session, document, "SALARIO", 2000.0, TransactionType.CREDIT, date(2026, 7, 5))
    _txn(session, document, "EDP", 60.0, TransactionType.DEBIT, date(2026, 7, 10))
    _txn(session, document, "CONTINENTE", 40.0, TransactionType.DEBIT, date(2026, 8, 2))
    _txn(session, document, "REVOLUT TOPUP", 100.0, TransactionType.TRANSFER, date(2026, 7, 15))

    income, expense = _monthly_flow_totals(session, today=date(2026, 8, 10))

    assert income == {"2026-07": 2000.0}
    assert expense == {"2026-07": 60.0, "2026-08": 40.0}


def test_flow_kpis_now_and_drill_down_urls(session):
    document = _doc(session)
    _txn(session, document, "SALARIO", 2000.0, TransactionType.CREDIT, date(2026, 7, 5))
    _txn(session, document, "EDP", 60.0, TransactionType.DEBIT, date(2026, 7, 10))
    _txn(session, document, "CONTINENTE", 40.0, TransactionType.DEBIT, date(2026, 8, 2))

    income_monthly, expense_monthly = _monthly_flow_totals(session, today=date(2026, 8, 10))
    kpis = get_flow_kpis(session, date(2026, 8, 10), income_monthly, expense_monthly)

    by_label = {k.label: k for k in kpis}
    assert list(by_label) == ["Income", "Expenses", "Net flow"]

    assert by_label["Income"].value == 0.0  # nothing credited in August yet
    assert by_label["Expenses"].value == 40.0
    assert by_label["Net flow"].value == -40.0

    assert by_label["Income"].color == "green"
    assert by_label["Expenses"].color == "red"
    assert by_label["Net flow"].color == "green"

    assert by_label["Income"].drill_down_url == "/transactions?category=income&date_from=2026-08-01&date_to=2026-08-10"
    assert by_label["Expenses"].drill_down_url == "/transactions?transaction_type=debit&date_from=2026-08-01&date_to=2026-08-10"
    assert by_label["Net flow"].drill_down_url == "/transactions?date_from=2026-08-01&date_to=2026-08-10"

    # July's complete-month totals feed the 1M trend point.
    assert by_label["Income"].chart.points[1].value == 2000.0  # "1M" point
    assert by_label["Expenses"].chart.points[1].value == 60.0


def test_cash_kpi_nets_credits_and_debits_across_tracked_accounts(session):
    account = Account(name="Santander", institution="Santander", account_type=AccountType.CHECKING)
    card_account = Account(name="Card", institution="Santander", account_type=AccountType.CARD)
    session.add_all([account, card_account])
    session.commit()
    session.refresh(account)
    session.refresh(card_account)

    document = _doc(session)
    t1 = _txn(session, document, "SALARIO", 2000.0, TransactionType.CREDIT, date(2026, 7, 5))
    t1.account_id = account.id
    t2 = _txn(session, document, "EDP", 300.0, TransactionType.DEBIT, date(2026, 7, 10))
    t2.account_id = account.id
    t3 = _txn(session, document, "CARD SPEND", 9999.0, TransactionType.DEBIT, date(2026, 7, 12))
    t3.account_id = card_account.id  # CARD accounts aren't "cash" -- must not count
    session.add_all([t1, t2, t3])
    session.commit()

    kpi = get_cash_kpi(session, today=date(2026, 8, 1))

    assert kpi.label == "Cash"
    assert kpi.value == 1700.0
    assert kpi.color == "green"
    assert kpi.drill_down_url == "/transactions"


def test_cash_kpi_has_no_historical_bars_with_only_one_month_of_data(session):
    account = Account(name="Santander", institution="Santander", account_type=AccountType.CHECKING)
    session.add(account)
    session.commit()
    session.refresh(account)

    document = _doc(session)
    t1 = _txn(session, document, "SALARIO", 500.0, TransactionType.CREDIT, date(2026, 8, 1))
    t1.account_id = account.id
    session.add(t1)
    session.commit()

    kpi = get_cash_kpi(session, today=date(2026, 8, 15))

    # Only the current month has any transaction -- no COMPLETE month exists yet,
    # so the trend chart is legitimately in its "no history" empty state.
    assert kpi.chart.has_data is False
    assert kpi.value == 500.0


def test_debt_kpi_sums_formal_and_owed_by_us_informal(session):
    session.add(Debt(kind=DebtKind.FORMAL, original_amount=100000.0, current_balance=Decimal("95000.00")))
    session.add(Debt(
        kind=DebtKind.INFORMAL, direction=DebtDirection.OWED_BY_US,
        original_amount=500.0, current_balance=Decimal("300.00"),
    ))
    session.add(Debt(
        kind=DebtKind.INFORMAL, direction=DebtDirection.OWED_TO_US,
        original_amount=200.0, current_balance=Decimal("200.00"),
    ))
    session.commit()

    kpi = get_debt_kpi(session, today=date(2026, 8, 1))

    assert kpi.label == "Debt"
    assert kpi.value == 95100.0  # 95000 + 300 - 200
    assert kpi.color == "red"
    assert kpi.drill_down_url == "/transactions/needs-review"
    assert kpi.chart.has_data is False  # no balance-history tracking exists (Ruling R2)


def test_debt_kpi_with_no_debts_is_a_clean_zero(session):
    kpi = get_debt_kpi(session, today=date(2026, 8, 1))

    assert kpi.value == 0.0
    assert kpi.chart.has_data is False


def test_debt_kpi_floors_net_negative_at_zero(session):
    session.add(Debt(
        kind=DebtKind.INFORMAL, direction=DebtDirection.OWED_TO_US,
        original_amount=5000.0, current_balance=Decimal("5000.00"),
    ))
    session.commit()

    kpi = get_debt_kpi(session, today=date(2026, 8, 1))

    assert kpi.value == 0.0  # Ruling R10 -- never shown as a negative "debt"


def test_yearly_commitments_progress_and_next_item(session):
    imi = Commitment(name="IMI", cadence=Cadence.YEARLY, planned_amount=600.0, year=2026, next_due_date=date(2026, 7, 1))
    vacation = Commitment(name="Vacation", cadence=Cadence.YEARLY, planned_amount=2000.0, year=2026, next_due_date=date(2026, 11, 30))
    session.add_all([imi, vacation])
    session.commit()
    session.refresh(imi)
    session.refresh(vacation)

    document = _doc(session)
    t1 = _txn(session, document, "AT IMI 1st installment", 300.0, TransactionType.DEBIT, date(2026, 4, 30))
    t1.commitment_id = imi.id
    session.add(t1)
    session.commit()

    card = get_yearly_commitments_card(session, today=date(2026, 6, 1))

    assert card.has_commitments is True
    assert card.planned_total == 2600.0
    assert card.actual_total == 300.0
    assert card.pct_of_plan == round(300.0 / 2600.0 * 100.0, 1)
    assert card.pct_of_year_elapsed == round(152 / 365 * 100.0, 1)  # day 152 of 2026 (not a leap year)
    assert card.next_item_label == "IMI"
    assert card.next_item_date == date(2026, 7, 1)
    assert card.next_item_url == f"/transactions?commitment_id={imi.id}"
    assert card.drill_down_url == "/transactions?date_from=2026-01-01&date_to=2026-12-31"


def test_yearly_commitments_empty_state(session):
    card = get_yearly_commitments_card(session, today=date(2026, 6, 1))

    assert card.has_commitments is False
    assert card.planned_total == 0.0
    assert card.pct_of_plan is None
    assert card.next_item_label is None


def test_category_comparison_ranked_with_delta(session):
    document = _doc(session)
    # This month: groceries 100, restaurants 50
    _txn(session, document, "CONTINENTE", 100.0, TransactionType.DEBIT, date(2026, 8, 5), Category.GROCERIES)
    _txn(session, document, "RESTAURANT A", 50.0, TransactionType.DEBIT, date(2026, 8, 6), Category.RESTAURANTS)
    # Prior 3 months: groceries averages to 80, restaurants to 100
    for m, amt in [(7, 90.0), (6, 80.0), (5, 70.0)]:
        _txn(session, document, "CONTINENTE", amt, TransactionType.DEBIT, date(2026, m, 5), Category.GROCERIES)
    for m, amt in [(7, 100.0), (6, 100.0), (5, 100.0)]:
        _txn(session, document, "RESTAURANT A", amt, TransactionType.DEBIT, date(2026, m, 6), Category.RESTAURANTS)

    rows = get_category_comparison(session, today=date(2026, 8, 10))

    by_cat = {r.category: r for r in rows}
    assert by_cat["groceries"].current_value == 100.0
    assert by_cat["groceries"].rolling_avg_value == 80.0
    assert by_cat["groceries"].delta_pct == 25.0
    assert by_cat["restaurants"].current_value == 50.0
    assert by_cat["restaurants"].rolling_avg_value == 100.0
    assert by_cat["restaurants"].delta_pct == -50.0
    # Ranked by current value descending.
    assert [r.category for r in rows] == ["groceries", "restaurants"]
    assert by_cat["groceries"].bar_pct == 100.0
    assert by_cat["restaurants"].bar_pct == 50.0
    assert by_cat["groceries"].drill_down_url == "/transactions?category=groceries&date_from=2026-08-01&date_to=2026-08-10"


def test_category_comparison_excludes_transfers_and_atm(session):
    document = _doc(session)
    _txn(session, document, "REVOLUT TOPUP", 200.0, TransactionType.TRANSFER, date(2026, 8, 5), Category.TRANSFER)
    _txn(session, document, "ATM", 40.0, TransactionType.DEBIT, date(2026, 8, 5), Category.ATM_WITHDRAWAL)

    rows = get_category_comparison(session, today=date(2026, 8, 10))

    assert rows == []


def test_category_comparison_excludes_zero_zero_categories(session):
    document = _doc(session)
    # A transaction from April, which is outside both the current month (Aug)
    # and the 3-month rolling history (May, June, July).
    # This triggers the zero-zero exclusion branch in get_category_comparison().
    _txn(session, document, "SHOPPING", 75.0, TransactionType.DEBIT, date(2026, 4, 15), Category.SHOPPING)

    rows = get_category_comparison(session, today=date(2026, 8, 24))

    # The shopping category should not appear in the results because
    # current_value (0.0) == 0 and rolling_avg (0.0) == 0
    assert "shopping" not in [r.category for r in rows]
