from datetime import date
from decimal import Decimal

from app.models.account import Account, AccountType
from app.models.commitment import Cadence, Commitment
from app.models.debt import Debt, DebtDirection, DebtKind
from app.models.document import Document, DocumentSource, DocumentStatus
from app.models.merchant import Merchant
from app.models.transaction import Category, Transaction, TransactionType
from app.services.overview_service import get_flow_kpis, _monthly_flow_totals, get_cash_kpi, get_debt_kpi, get_yearly_commitments_card, get_category_comparison, CategoryComparisonRow, get_narrative_insight, get_needs_attention, get_overview_data, get_period_dependent_data, _resolve_lookback_months


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

    assert by_label["Income"].drill_down_url == "/financials/transactions?transaction_type=credit&date_from=2026-08-01&date_to=2026-08-10"
    assert by_label["Expenses"].drill_down_url == "/financials/transactions?transaction_type=debit&date_from=2026-08-01&date_to=2026-08-10"
    assert by_label["Net flow"].drill_down_url == "/financials/transactions?date_from=2026-08-01&date_to=2026-08-10"

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
    assert kpi.drill_down_url == "/financials/transactions"


def test_cash_kpi_has_a_non_bank_balance_caption(session):
    # Ruling R1: Cash is a reconstructed net cash flow since the earliest
    # ingested transaction, not a live bank balance -- the KPI card must
    # carry a caption disclosing this so it isn't mistaken for one.
    kpi = get_cash_kpi(session, today=date(2026, 8, 1))

    assert kpi.caption
    assert "not a live bank balance" in kpi.caption


def test_only_cash_kpi_has_a_caption(session):
    document = _doc(session)
    _txn(session, document, "SALARIO", 2000.0, TransactionType.CREDIT, date(2026, 7, 5))
    _txn(session, document, "EDP", 60.0, TransactionType.DEBIT, date(2026, 7, 10))

    income_monthly, expense_monthly = _monthly_flow_totals(session, today=date(2026, 8, 10))
    flow_kpis = get_flow_kpis(session, date(2026, 8, 10), income_monthly, expense_monthly)
    debt_kpi = get_debt_kpi(session, today=date(2026, 8, 10))

    for kpi in flow_kpis:
        assert kpi.caption is None
    assert debt_kpi.caption is None


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


def test_debt_kpi_treats_informal_none_direction_as_owed_by_us(session):
    # direction is Optional on Debt -- an INFORMAL debt someone forgot to
    # set a direction on must not silently contribute €0; the common-case
    # assumption is that it's a real liability (owed BY us), not owed to us.
    session.add(Debt(kind=DebtKind.INFORMAL, direction=None, original_amount=400.0, current_balance=Decimal("400.00")))
    session.commit()

    kpi = get_debt_kpi(session, today=date(2026, 8, 1))

    assert kpi.value == 400.0
    assert kpi.drill_down_url == "/financials/transactions/needs-review"
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
    assert card.next_item_url == f"/financials/transactions?commitment_id={imi.id}"
    assert card.drill_down_url == "/financials/transactions?date_from=2026-01-01&date_to=2026-12-31"


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
    assert by_cat["groceries"].drill_down_url == "/financials/transactions?category=groceries&date_from=2026-08-01&date_to=2026-08-10"


def test_category_comparison_rolling_months_is_configurable(session):
    document = _doc(session)
    _txn(session, document, "CONTINENTE", 100.0, TransactionType.DEBIT, date(2026, 8, 5), Category.GROCERIES)
    # 3-month history (May-Jul): averages to 80. 6-month history (Feb-Jul)
    # adds Feb-Apr at 40 each, pulling the average down to 60.
    for m, amt in [(7, 90.0), (6, 80.0), (5, 70.0)]:
        _txn(session, document, "CONTINENTE", amt, TransactionType.DEBIT, date(2026, m, 5), Category.GROCERIES)
    for m, amt in [(4, 40.0), (3, 40.0), (2, 40.0)]:
        _txn(session, document, "CONTINENTE", amt, TransactionType.DEBIT, date(2026, m, 5), Category.GROCERIES)

    default_rows = get_category_comparison(session, today=date(2026, 8, 10))
    six_month_rows = get_category_comparison(session, today=date(2026, 8, 10), rolling_months=6)

    assert {r.category: r for r in default_rows}["groceries"].rolling_avg_value == 80.0
    assert {r.category: r for r in six_month_rows}["groceries"].rolling_avg_value == 60.0


def test_resolve_lookback_months_maps_known_ranges():
    today = date(2026, 8, 10)
    assert _resolve_lookback_months("1m", today) == 1
    assert _resolve_lookback_months("6m", today) == 6
    assert _resolve_lookback_months("12m", today) == 12


def test_resolve_lookback_months_ytd_uses_elapsed_months():
    assert _resolve_lookback_months("ytd", date(2026, 8, 10)) == 7
    # January: no complete elapsed months this year -- floors to 1, not 0.
    assert _resolve_lookback_months("ytd", date(2026, 1, 15)) == 1


def test_period_dependent_data_links_chart_and_table_to_same_range(session):
    document = _doc(session)
    _txn(session, document, "CONTINENTE", 100.0, TransactionType.DEBIT, date(2026, 8, 5), Category.GROCERIES)
    for m, amt in [(7, 90.0), (6, 80.0), (5, 70.0), (4, 40.0), (3, 40.0), (2, 40.0)]:
        _txn(session, document, "CONTINENTE", amt, TransactionType.DEBIT, date(2026, m, 5), Category.GROCERIES)

    chart, rows = get_period_dependent_data(session, date(2026, 8, 10), "6m")

    assert chart.range_key == "6m"
    assert {r.category: r for r in rows}["groceries"].rolling_avg_value == 60.0


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


def test_narrative_insight_names_biggest_drop_and_rise():
    rows = [
        CategoryComparisonRow("travel", current_value=0.0, rolling_avg_value=600.0, delta_pct=-100.0, bar_pct=0.0, drill_down_url=""),
        CategoryComparisonRow("restaurants", current_value=220.0, rolling_avg_value=100.0, delta_pct=120.0, bar_pct=100.0, drill_down_url=""),
        CategoryComparisonRow("groceries", current_value=300.0, rolling_avg_value=300.0, delta_pct=0.0, bar_pct=100.0, drill_down_url=""),
    ]
    # total_current = 520, total_avg = 1000 -> -48.0% overall

    sentence = get_narrative_insight(rows)

    assert sentence is not None
    assert "fell 48.0%" in sentence
    assert "Travel" in sentence
    assert "Restaurants" in sentence


def test_narrative_insight_none_when_no_history():
    assert get_narrative_insight([]) is None


def test_narrative_insight_none_when_total_avg_is_zero():
    rows = [
        CategoryComparisonRow("groceries", current_value=100.0, rolling_avg_value=0.0, delta_pct=None, bar_pct=100.0, drill_down_url=""),
        CategoryComparisonRow("restaurants", current_value=50.0, rolling_avg_value=0.0, delta_pct=None, bar_pct=50.0, drill_down_url=""),
    ]
    # total_current = 150, total_avg = 0 -> should return None (prevents division-by-zero)

    sentence = get_narrative_insight(rows)

    assert sentence is None


def test_narrative_insight_single_row_drop_only():
    rows = [
        CategoryComparisonRow("travel", current_value=100.0, rolling_avg_value=200.0, delta_pct=-50.0, bar_pct=100.0, drill_down_url=""),
    ]
    # total_current = 100, total_avg = 200 -> -50.0% overall, drop only, no rise

    sentence = get_narrative_insight(rows)

    assert sentence is not None
    assert "fell 50.0%" in sentence
    assert "Travel" in sentence
    assert "of the change" in sentence
    assert "offset by" not in sentence  # ensure no rise phrase when only drop exists


def test_narrative_insight_single_row_rise_only():
    rows = [
        CategoryComparisonRow("restaurants", current_value=300.0, rolling_avg_value=200.0, delta_pct=50.0, bar_pct=100.0, drill_down_url=""),
    ]
    # total_current = 300, total_avg = 200 -> 50.0% overall, rise only, no drop

    sentence = get_narrative_insight(rows)

    assert sentence is not None
    assert "rose 50.0%" in sentence
    assert "Restaurants" in sentence
    assert "of the increase" in sentence
    assert "accounted for" not in sentence or "reduction" not in sentence  # no drop phrase


def test_narrative_insight_title_cases_underscored_category_names():
    rows = [
        CategoryComparisonRow("other_expense", current_value=50.0, rolling_avg_value=200.0, delta_pct=-75.0, bar_pct=25.0, drill_down_url=""),
        CategoryComparisonRow("travel_dining", current_value=300.0, rolling_avg_value=100.0, delta_pct=200.0, bar_pct=100.0, drill_down_url=""),
    ]
    # total_current = 350, total_avg = 300 -> 16.7% overall
    # biggest drop: other_expense (50 - 200 = -150)
    # biggest rise: travel_dining (300 - 100 = 200)

    sentence = get_narrative_insight(rows)

    assert sentence is not None
    assert "rose 16.7%" in sentence
    assert "Other Expense" in sentence  # underscore replaced with space, title-cased
    assert "Travel Dining" in sentence  # same for this category
    assert "other_expense" not in sentence  # raw underscore form should not appear
    assert "travel_dining" not in sentence


def test_needs_attention_combines_review_queue_upcoming_bill_anomaly_and_documents(session):
    # Review queue: one unconfirmed merchant.
    session.add(Merchant(canonical_name="New Shop", normalized_key="new shop", confirmed=False))
    # Upcoming bill within the lookahead window.
    commitment = Commitment(
        name="Car insurance", cadence=Cadence.YEARLY, planned_amount=400.0,
        year=2026, next_due_date=date(2026, 8, 20),
    )
    session.add(commitment)
    # A needs-attention document.
    session.add(Document(
        filename="bad.pdf", file_path="/tmp/bad.pdf", content_hash="hbad",
        source=DocumentSource.MANUAL, status=DocumentStatus.NEEDS_ATTENTION, failure_reason="unreadable",
    ))
    session.commit()
    session.refresh(commitment)

    category_rows = [
        CategoryComparisonRow("shopping", current_value=200.0, rolling_avg_value=100.0, delta_pct=100.0, bar_pct=100.0, drill_down_url="/financials/transactions?category=shopping"),
    ]

    items = get_needs_attention(session, today=date(2026, 8, 10), category_rows=category_rows)
    kinds = {i.kind for i in items}

    assert "review_queue" in kinds
    assert "upcoming_bill" in kinds
    assert "category_anomaly" in kinds
    assert "document" in kinds

    upcoming = next(i for i in items if i.kind == "upcoming_bill")
    assert upcoming.url == f"/financials/transactions?commitment_id={commitment.id}"
    doc_item = next(i for i in items if i.kind == "document")
    assert doc_item.url.startswith("/financials/bills/")


def test_needs_attention_skips_small_anomalies_below_floor():
    category_rows = [
        CategoryComparisonRow("shopping", current_value=10.0, rolling_avg_value=5.0, delta_pct=100.0, bar_pct=100.0, drill_down_url=""),
    ]
    # rolling_avg_value (5.0) is below the €30 noise floor -- must not fire.
    items = get_needs_attention(None, today=date(2026, 8, 10), category_rows=category_rows)
    assert all(i.kind != "category_anomaly" for i in items)


def test_get_overview_data_suppresses_narrative_in_first_3_days_of_month(session):
    document = _doc(session)
    # This month (posted on day 1, so it's present regardless of which
    # "today" we ask about below): a category spend that would otherwise
    # produce a narrative sentence.
    _txn(session, document, "RESTAURANT A", 10.0, TransactionType.DEBIT, date(2026, 8, 1), Category.RESTAURANTS)
    # Prior 3 months: restaurants averages to 100 -- a steep, narrative-worthy drop.
    for m, amt in [(7, 100.0), (6, 100.0), (5, 100.0)]:
        _txn(session, document, "RESTAURANT A", amt, TransactionType.DEBIT, date(2026, m, 6), Category.RESTAURANTS)

    early_month = get_overview_data(session, today=date(2026, 8, 2))
    later_in_month = get_overview_data(session, today=date(2026, 8, 10))

    # Early-month MTD spend is too partial to fairly compare against a full
    # prior-month average, so the banner is suppressed on day <= 3...
    assert early_month.narrative is None
    # ...even though the exact same data would otherwise produce one later
    # in the month, proving this is the gate at work, not an absence of data.
    assert later_in_month.narrative is not None


def test_get_overview_data_end_to_end(session):
    data = get_overview_data(session, today=date(2026, 8, 10))

    assert [k.label for k in data.flow_kpis] == ["Income", "Expenses", "Net flow"]
    assert [k.label for k in data.position_kpis] == ["Cash", "Debt"]
    assert data.yearly_commitments.has_commitments is False
    assert data.narrative is None  # no transaction history in this empty DB
    assert data.cash_flow_chart.range_key == "12m"
    assert data.category_comparison == []
    assert isinstance(data.needs_attention, list)
