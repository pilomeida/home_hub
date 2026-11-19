"""PLAN step 10a: one chart component on one scale function. For every chart already on the Hub, built from the code
that feeds its page: the exaggeration cap holds, no current-month value is past today, no forecast or budget is drawn."""

import random
import re
from datetime import date, timedelta

import pytest

from app.models.transaction import TransactionType
from app.services.overview_charts import cash_flow_items, same_day
from app.services.overview_service import get_overview_data
from app.ui import charts
from app.ui.charts import MAX_EXAGGERATION, Bar, Item, exaggeration, make_scale
from tests.test_overview_service import _doc, _txn

TODAY = date(2026, 11, 19)


def _random_sets():
    rnd = random.Random(10)
    sets = [[2310, 2450, 2290, 2184], [548, 562, 530], [5, 5, 5], [0, 10], [13.4, 13.9, 16], [100, 101, 102],
            [0.14, 0.5], [1, 1000], [4.4, 4.3, 4.6]]
    for _ in range(300):
        base = rnd.choice([0.1, 3, 50, 900, 20000])
        sets.append([round(base * rnd.uniform(0.2, 3), 2) for _ in range(rnd.randint(2, 14))])
    return sets


@pytest.mark.parametrize("values", _random_sets())
def test_the_floor_never_exaggerates_and_the_axis_has_real_values(values):
    scale = make_scale(values)
    assert exaggeration(scale, values) <= MAX_EXAGGERATION + 1e-9
    assert scale.floor <= min(values) and scale.top >= max(values)
    assert scale.floor == 0 or scale.floor <= min(values) * (1 - 1 / MAX_EXAGGERATION) + 1e-9
    assert list(scale.ticks) == sorted(scale.ticks) and scale.ticks[0] == scale.floor


def test_negative_values_span_zero():
    scale = make_scale([-300, 1200, 450])
    assert scale.floor <= -300 and 0 in scale.ticks and scale.top >= 1200


def _tick_labels(svg: str) -> list[str]:
    return re.findall(r'text-anchor="end" font-size="9.5" fill="var\(--muted\)">([^<]+)<', svg)


def test_every_type_draws_its_axis_values_and_no_legend():
    items = [Item("Sep", 1800, full=2300), Item("Oct", 1900, full=2450), Item("Nov", 2100, current=True)]
    for svg in (charts.columns(items), charts.line(items, trend=True)):
        assert "<svg" in svg and _tick_labels(svg) and "legend" not in svg.lower()
    hb = charts.bars([Bar("Food", 400, 612), Bar("Transport", 120, 210)])
    assert "€400" in hb and "last month €612" in hb


def test_a_reference_range_is_a_band_and_a_value_outside_it_is_red():
    svg = charts.line([Item("2024-11-11", 16.0), Item("2026-03-01", 18.1, current=True)], fmt=charts.plain,
                      ref=(13.0, 17.0))
    assert charts.BAND in svg and charts.BAD_NOW in svg and "reference range 13 to 17" in svg


def _world(session):
    doc = _doc(session)
    for months_back, day, amount in [(2, 5, 900.0), (2, 25, 300.0), (1, 3, 800.0), (1, 28, 400.0), (0, 2, 700.0)]:
        y, m = TODAY.year, TODAY.month - months_back
        _txn(session, doc, "SHOP", amount, TransactionType.DEBIT, date(y, m, day))
        _txn(session, doc, "SALARY", amount * 2, TransactionType.CREDIT, date(y, m, day))
    _txn(session, doc, "TOMORROW", 5000.0, TransactionType.DEBIT, TODAY + timedelta(days=1))   # not happened yet
    return doc


def _check_items(items, today=TODAY):
    current = [i for i in items if i.current]
    assert len(current) <= 1 and all(i.full is None for i in current)          # the current month: so far only
    for i in items:
        if i.full is not None:
            assert i.value <= i.full + 1e-9                                       # same day never above the full month
    values = [v for i in items for v in (i.full, i.value) if v is not None]
    if values and min(values) > 0:
        assert exaggeration(make_scale(values), values) <= MAX_EXAGGERATION + 1e-9


def test_the_overview_charts_follow_the_rule(session):
    _world(session)
    data = get_overview_data(session, today=TODAY, cash_flow_range="3m")
    expense = cash_flow_items(data.cash_flow_chart, "expense")
    assert [i.label for i in expense] == ["Sep", "Oct", "Nov"]
    assert expense[-1].current and expense[-1].value == 700.0                    # tomorrow's 5,000 is not drawn
    assert expense[0].full == 1200.0 and expense[0].value == 900.0               # the 25th is after the 19th
    assert expense[1].full == 1200.0 and expense[1].value == 800.0
    _check_items(expense)
    _check_items(cash_flow_items(data.cash_flow_chart, "income"))
    for card in data.flow_kpis:
        _check_items(card.chart.items)
        assert card.chart.items[0].label == "Now" and card.chart.items[0].current
    spent = next(c for c in data.flow_kpis if c.label == "Expenses")
    assert spent.chart.items[0].value == 700.0


def test_the_overview_page_draws_with_the_component_and_no_budget(client, session):
    _world(session)
    html = client.get("/").text
    assert html.count('class="chart"') >= 3                                       # KPI cards, income, expenses
    assert "fc-cf-bar" not in html and "fc-bar-slot" not in html                 # the old hand-drawn bars are gone
    assert "budget" not in " ".join(re.findall(r"<svg.*?</svg>", html, re.S)).lower()


def test_same_day_is_the_last_day_of_a_shorter_month():
    assert same_day("2026-02", date(2026, 3, 31)) == date(2026, 2, 28)
    assert same_day("2026-10", TODAY) == date(2026, 10, 19)


def test_the_utilities_charts_draw_with_the_component(client, session):
    from app.models.utility_reading import UtilityReading, UtilityType
    from tests.test_utilities_router import _make_document
    for i, (label, kwh, cost) in enumerate([("2026-08", 210.0, 48.2), ("2026-09", 198.0, 45.9), ("2026-10", 230.0, 52.4)]):
        doc = _make_document(session, f"edp-{i}")
        session.add(UtilityReading(document_id=doc.id, utility_type=UtilityType.ELECTRICITY, period_label=label,
                                   consumption_value=kwh, consumption_unit="kWh", cost_total=cost))
    session.commit()
    html = client.get("/financials/utilities/electricity").text
    assert 'class="chart"' in html and "chart-bar-fill" not in html
    svgs = re.findall(r"<svg.*?</svg>", html, re.S)
    assert any("230.00 kWh" in s for s in svgs)
