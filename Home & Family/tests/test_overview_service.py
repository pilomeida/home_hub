from datetime import date
from decimal import Decimal

from app.models.account import Account, AccountType
from app.models.debt import Debt, DebtDirection, DebtKind
from app.models.document import Document, DocumentSource
from app.models.transaction import Category, Transaction, TransactionType
from app.services.overview_service import get_flow_kpis, _monthly_flow_totals, get_cash_kpi, get_debt_kpi


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
