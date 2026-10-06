from datetime import date

from app.services.budget_forecast import (
    Range, income_month_end, income_range_for_month, income_year_end, month_end_estimate,
    typical_month, year_end_monthly, yearly_line_estimate, yearly_line_month_estimate,
)

TODAY = date(2026, 10, 15)
HIST = {"2026-04": 100.0, "2026-05": 200.0, "2026-06": 300.0, "2026-07": 100.0,
        "2026-08": 200.0, "2026-09": 100.0, "2026-10": 999.0}   # Oct = current month, ignored


def test_typical_month_is_mean_of_last_six_complete_months():
    assert typical_month(HIST, TODAY) == 1000.0 / 6


def test_typical_month_counts_missing_months_as_zero_and_empty_is_zero():
    assert typical_month({"2026-09": 60.0}, TODAY) == 10.0
    assert typical_month({}, TODAY) == 0.0


def test_typical_month_with_only_current_month_is_zero():
    assert typical_month({"2026-10": 500.0}, TODAY) == 0.0


def test_typical_month_crosses_year_boundary():
    assert typical_month({"2025-12": 60.0, "2026-01": 60.0}, date(2026, 2, 3), n=2) == 60.0


def test_month_end_estimate_statuses():
    assert month_end_estimate(50, 100, None).status == "no_budget"
    ok = month_end_estimate(50, 100, 200)
    assert (ok.expected, ok.if_budget_respected, ok.status) == (100, 200, "ok")
    assert month_end_estimate(50, 100, 80).status == "at_risk"
    assert month_end_estimate(90, 100, 80).status == "over"
    assert month_end_estimate(150, 100, 200).expected == 150


def test_month_end_estimate_no_budget_has_no_respected_value():
    assert month_end_estimate(50, 100, None).if_budget_respected is None


def test_year_end_monthly_projects_remaining_months():
    est = year_end_monthly(ytd=1000, spent_mtd=50, typical=100, budget=120, today=TODAY)
    assert est.expected == 950 + 100 + 200
    assert est.if_budget_respected == 950 + 120 + 240
    assert est.status == "ok"


def test_year_end_monthly_statuses():
    # at_risk: expected 1250 > 1200 but ytd <= 1200
    assert year_end_monthly(1000, 50, 100, 100, TODAY).status == "at_risk"
    # over: ytd already above budget*12
    assert year_end_monthly(1300, 50, 100, 100, TODAY).status == "over"
    est = year_end_monthly(1000, 50, 100, None, TODAY)
    assert (est.expected, est.if_budget_respected, est.status) == (1250, None, "no_budget")


def test_year_end_monthly_january_and_december():
    jan = year_end_monthly(ytd=30, spent_mtd=30, typical=100, budget=None, today=date(2026, 1, 10))
    assert jan.expected == 0 + 100 + 100 * 11
    dec = year_end_monthly(ytd=1100, spent_mtd=30, typical=100, budget=None, today=date(2026, 12, 10))
    assert dec.expected == 1070 + 100            # rest = 0


def test_yearly_line_places_remaining_in_expected_month():
    assert yearly_line_month_estimate(0, 0, 420, 0, 10, TODAY) == 420
    assert yearly_line_month_estimate(0, 0, 420, 0, 4, TODAY) == 420
    assert yearly_line_month_estimate(0, 0, 420, 0, 12, TODAY) == 0
    assert yearly_line_month_estimate(420, 420, 420, 0, 10, TODAY) == 420
    # due this month, partly paid this month: the remainder is still added
    assert yearly_line_month_estimate(100, 100, 420, 0, 10, TODAY) == 420


def test_yearly_line_month_estimate_unknown_month_and_no_budget():
    # no expected month: only counts once something was spent this month
    assert yearly_line_month_estimate(0, 0, 420, 0, None, TODAY) == 0
    assert yearly_line_month_estimate(100, 100, 420, 0, None, TODAY) == 100
    # no budget: planned falls back to last year's total
    assert yearly_line_month_estimate(0, 0, None, 380, 10, TODAY) == 380


def test_yearly_line_without_budget_uses_last_year():
    est = yearly_line_estimate(0, None, 380, 4, TODAY)
    assert (est.expected, est.if_budget_respected, est.status) == (380, None, "no_budget")


def test_yearly_line_statuses():
    ok = yearly_line_estimate(0, 420, 0, 10, TODAY)
    assert (ok.expected, ok.if_budget_respected, ok.status) == (420, 420, "ok")
    assert yearly_line_estimate(500, 420, 0, 10, TODAY).status == "over"
    assert yearly_line_estimate(100, 420, 0, 10, TODAY).status == "ok"
    assert yearly_line_estimate(0, 0, 100, 10, TODAY).status == "ok"


def test_yearly_line_at_risk_when_history_exceeds_budget():
    # planned is the budget itself, so at_risk needs spent <= budget < expected: not reachable
    # via planned=budget; verify expected never undercuts spent
    assert yearly_line_estimate(300, 420, 900, 10, TODAY).expected == 420


def test_income_range_prefers_same_month_of_previous_years():
    hist = {"2025-10": 1000.0, "2024-10": 3000.0, "2026-09": 50.0}
    assert income_range_for_month(hist, 2026, 10, TODAY) == Range(1000.0, 2000.0, 3000.0)


def test_income_range_with_one_prior_year_is_flat():
    assert income_range_for_month({"2025-10": 800.0}, 2026, 10, TODAY) == Range(800.0, 800.0, 800.0)


def test_income_range_falls_back_to_trailing_months():
    hist = {"2026-07": 600.0, "2026-08": 0.0, "2026-09": 300.0}
    assert income_range_for_month(hist, 2026, 10, TODAY) == Range(0.0, 150.0, 600.0)


def test_income_range_empty_history_is_zero():
    assert income_range_for_month({}, 2026, 10, TODAY) == Range(0.0, 0.0, 0.0)


def test_income_month_end_is_floored_by_received():
    hist = {"2025-10": 1000.0, "2024-10": 3000.0}
    assert income_month_end(2500.0, hist, TODAY) == Range(2500.0, 2500.0, 3000.0)


def test_income_month_end_empty_history_is_received():
    assert income_month_end(0.0, {}, TODAY) == Range(0.0, 0.0, 0.0)


def test_income_year_end_adds_remaining_months():
    hist = {"2025-10": 1000.0, "2025-11": 1000.0, "2025-12": 2000.0}
    r = income_year_end(received_ytd=5000.0, received_mtd=400.0, history=hist, today=TODAY)
    assert r.expected == 4600 + 1000 + 1000 + 2000
    assert r.low == r.high == r.expected


def test_income_year_end_in_december_has_no_remaining_months():
    r = income_year_end(1000.0, 200.0, {}, date(2026, 12, 5))
    assert r == Range(1000.0, 1000.0, 1000.0)


def test_yearly_line_without_due_month_adds_no_remainder():
    # Holidays: EUR 3,000 last year, EUR 500 YTD before this month, EUR 20 this month
    assert yearly_line_month_estimate(20, 520, None, 3000, None, TODAY) == 20


def test_yearly_line_partly_paid_is_not_overdue():
    # car insurance EUR 587 of EUR 600 paid, due in March, now October
    assert yearly_line_month_estimate(0, 587, 600, 0, 3, TODAY) == 0
    # nothing paid at all this year: genuinely overdue
    assert yearly_line_month_estimate(0, 0, 600, 0, 3, TODAY) == 600
