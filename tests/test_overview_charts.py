from datetime import date

from app.services.overview_charts import build_trend_chart, build_cash_flow_chart


def test_all_positive_values_scale_between_floor_and_max():
    monthly = {
        "2026-07": 100.0, "2026-06": 100.0, "2026-05": 100.0, "2026-04": 100.0,
        "2026-03": 100.0, "2026-02": 100.0, "2026-01": 100.0, "2025-12": 100.0,
        "2025-11": 100.0, "2025-10": 100.0, "2025-09": 100.0, "2025-08": 200.0,
    }
    chart = build_trend_chart(monthly, today=date(2026, 8, 15), mtd_value=150.0)

    assert chart.has_data is True
    assert chart.bidirectional is False
    labels = [p.label for p in chart.points]
    assert labels == ["Now", "1M", "3M", "6M", "12M", "18M", "24M"]
    assert all(p.direction == "up" for p in chart.points)
    assert all(0.0 <= p.height_pct <= 100.0 for p in chart.points)
    # The highest raw value in the whole set should render at 100%.
    assert max(p.height_pct for p in chart.points) == 100.0
    # 18M/24M are flagged distant for the highlight box; nothing else is.
    assert [p.is_distant for p in chart.points] == [False, False, False, False, False, True, True]
    now_point = chart.points[0]
    assert now_point.is_now is True
    assert now_point.value == 150.0


def test_negative_values_render_bidirectional():
    monthly = {"2026-07": -50.0, "2026-06": 200.0}
    chart = build_trend_chart(monthly, today=date(2026, 8, 1), mtd_value=-10.0)

    assert chart.bidirectional is True
    by_label = {p.label: p for p in chart.points}
    assert by_label["Now"].direction == "down"
    assert by_label["1M"].direction == "down"  # last complete month (2026-07) is negative
    assert by_label["3M"].direction == "up"     # avg of 2026-07 (-50) and 2026-06 (200) = 75, positive


def test_empty_monthly_totals_is_a_graceful_empty_state():
    chart = build_trend_chart({}, today=date(2026, 8, 1), mtd_value=0.0)

    assert chart.has_data is False
    assert len(chart.points) == 7


def test_floor_keeps_smallest_bar_visible():
    monthly = {f"2026-{m:02d}": 1000.0 for m in range(1, 8)}
    monthly["2025-08"] = 1070.0  # only the 24M horizon differs, ~7% swing like real Income data
    chart = build_trend_chart(monthly, today=date(2026, 8, 15), mtd_value=1000.0)

    smallest = min(p.height_pct for p in chart.points)
    assert smallest > 0.0  # never fully disappears


def test_cash_flow_chart_12m_includes_current_month_to_date():
    income = {"2026-07": 2000.0}
    expense = {"2026-07": 1500.0, "2026-08": 300.0}
    chart = build_cash_flow_chart(income, expense, "12m", today=date(2026, 8, 10))

    assert len(chart.months) == 12
    assert chart.months[-1].label == "2026-08"
    assert chart.months[-1].expense == 300.0
    assert chart.months[-2].label == "2026-07"
    assert chart.months[-2].income == 2000.0


def test_cash_flow_chart_ytd_starts_in_january():
    chart = build_cash_flow_chart({}, {}, "ytd", today=date(2026, 3, 15))

    assert [m.label for m in chart.months] == ["2026-01", "2026-02", "2026-03"]


def test_cash_flow_chart_unknown_range_falls_back_to_12m():
    chart = build_cash_flow_chart({}, {}, "bogus", today=date(2026, 8, 1))

    assert chart.range_key == "12m"
    assert len(chart.months) == 12


def test_cash_flow_chart_max_value_covers_both_series():
    income = {"2026-08": 100.0}
    expense = {"2026-08": 400.0}
    chart = build_cash_flow_chart(income, expense, "1m", today=date(2026, 8, 1))

    assert chart.max_value == 400.0
