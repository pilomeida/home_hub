"""Budget forecast maths: pure functions, no database.

Month keys are "YYYY-MM" strings; `today` is a date.
"""
from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.services.overview_charts import _complete_months_before


@dataclass(frozen=True)
class Range:
    low: float
    expected: float
    high: float


@dataclass(frozen=True)
class LineEstimate:
    expected: float
    if_budget_respected: Optional[float]
    status: str  # "no_budget" | "ok" | "at_risk" | "over"


def _trailing_values(history: dict[str, float], today: date, n: int) -> list[float]:
    return [history.get(k, 0.0) for k in _complete_months_before(today, n)]


def typical_month(history: dict[str, float], today: date, n: int = 6) -> float:
    if not history or n <= 0:
        return 0.0
    values = _trailing_values(history, today, n)
    return sum(values) / len(values)


def _status(spent: float, expected: float, limit: Optional[float]) -> str:
    if limit is None:
        return "no_budget"
    if spent > limit:
        return "over"
    if expected > limit:
        return "at_risk"
    return "ok"


def month_end_estimate(spent_mtd: float, typical: float, budget: Optional[float]) -> LineEstimate:
    expected = max(spent_mtd, typical)
    respected = None if budget is None else max(spent_mtd, budget)
    return LineEstimate(expected, respected, _status(spent_mtd, expected, budget))


def year_end_monthly(ytd: float, spent_mtd: float, typical: float,
                     budget: Optional[float], today: date) -> LineEstimate:
    done = ytd - spent_mtd
    rest = 12 - today.month
    expected = done + max(spent_mtd, typical) + typical * rest
    if budget is None:
        return LineEstimate(expected, None, "no_budget")
    respected = done + max(spent_mtd, budget) + budget * rest
    return LineEstimate(expected, respected, _status(ytd, expected, budget * 12))


def _planned(budget: Optional[float], last_year_total: float) -> float:
    return budget if budget is not None else last_year_total


def yearly_line_estimate(spent_ytd: float, budget: Optional[float], last_year_total: float,
                         expected_month: Optional[int], today: date) -> LineEstimate:
    expected = max(spent_ytd, _planned(budget, last_year_total))
    respected = None if budget is None else max(spent_ytd, budget)
    return LineEstimate(expected, respected, _status(spent_ytd, expected, budget))


def yearly_due_now(spent_ytd: float, expected_month: Optional[int], today: date) -> bool:
    """Is a yearly bill's remainder due in the current month? Only with a due
    month: this month, or earlier AND nothing paid at all this year (genuinely
    overdue). A partly paid or early-paid bill is not treated as overdue."""
    if expected_month is None:
        return False
    if expected_month == today.month:
        return True
    return expected_month < today.month and spent_ytd == 0


def yearly_line_month_estimate(spent_mtd: float, spent_ytd: float, budget: Optional[float],
                               last_year_total: float, expected_month: Optional[int],
                               today: date) -> float:
    remaining = max(0.0, _planned(budget, last_year_total) - spent_ytd)
    due_now = yearly_due_now(spent_ytd, expected_month, today)
    return spent_mtd + (remaining if due_now and remaining > 0 else 0.0)


def _range_of(values: list[float]) -> Range:
    return Range(min(values), sum(values) / len(values), max(values))


def income_range_for_month(history: dict[str, float], year: int, month: int, today: date) -> Range:
    same = [history[k] for k in (f"{year - 1:04d}-{month:02d}", f"{year - 2:04d}-{month:02d}")
            if k in history]
    if same:
        return _range_of(same)
    if not history:
        return Range(0.0, 0.0, 0.0)
    return _range_of(_trailing_values(history, today, 6))


def income_month_end(received_mtd: float, history: dict[str, float], today: date) -> Range:
    r = income_range_for_month(history, today.year, today.month, today)
    return Range(max(received_mtd, r.low), max(received_mtd, r.expected), max(received_mtd, r.high))


def income_year_end(received_ytd: float, received_mtd: float, history: dict[str, float],
                    today: date) -> Range:
    done = received_ytd - received_mtd
    low = expected = high = done
    parts = [income_month_end(received_mtd, history, today)]
    parts += [income_range_for_month(history, today.year, m, today)
              for m in range(today.month + 1, 13)]
    for p in parts:
        low += p.low
        expected += p.expected
        high += p.high
    return Range(low, expected, high)
