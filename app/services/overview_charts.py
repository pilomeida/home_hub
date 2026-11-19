"""Pure-computation chart engine for the Overview screen's trend mini-charts
and 12-month cash-flow chart. No database access here on purpose: the
auto-scaling / dual-direction / floor-legibility math is the hardest part
of this screen to get right, so it's kept testable with plain dicts,
independent of any fixture or session."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

_HORIZONS = [1, 3, 6, 12, 18, 24]
_HORIZON_LABELS = {1: "1M", 3: "3M", 6: "6M", 12: "12M", 18: "18M", 24: "24M"}
_DISTANT_LABELS = {"18M", "24M"}


def _complete_months_before(today: date, n: int) -> list[str]:
    """The n most recent complete calendar months strictly before today's
    (in-progress) month, as 'YYYY-MM', most-recent first."""
    periods = []
    year, month = today.year, today.month
    for _ in range(n):
        month -= 1
        if month == 0:
            month, year = 12, year - 1
        periods.append(f"{year:04d}-{month:02d}")
    return periods


def _rolling_average(monthly_totals: dict[str, float], today: date, n: int) -> float:
    periods = _complete_months_before(today, n)
    values = [monthly_totals[p] for p in periods if p in monthly_totals]
    return sum(values) / len(values) if values else 0.0


@dataclass
class TrendPoint:
    label: str
    value: float
    is_now: bool = False
    is_distant: bool = False
    direction: str = "up"  # "up" or "down" -- which side of the zero-axis this bar sits on
    height_pct: float = 0.0  # 0-100, height within its own direction's region of the chart


@dataclass
class TrendChart:
    points: list[TrendPoint] = field(default_factory=list)
    bidirectional: bool = False
    has_data: bool = False
    items: list = field(default_factory=list)     # what the one chart component draws (PLAN step 10a)


def build_trend_chart(
    monthly_totals: dict[str, float],
    today: date,
    mtd_value: float,
    floor_fraction: float = 0.08,
) -> TrendChart:
    has_data = bool(monthly_totals)
    labels = ["Now"] + [_HORIZON_LABELS[n] for n in _HORIZONS]
    raw_values = [mtd_value] + [_rolling_average(monthly_totals, today, n) for n in _HORIZONS]

    min_value = min(raw_values)
    max_value = max(raw_values)
    bidirectional = min_value < 0.0

    points: list[TrendPoint] = []
    if bidirectional:
        up_extent = max(max_value, 0.0)
        down_extent = max(-min_value, 0.0)
        for label, value in zip(labels, raw_values):
            direction = "up" if value >= 0 else "down"
            extent = up_extent if direction == "up" else down_extent
            magnitude = abs(value)
            if extent <= 0:
                height_pct = 0.0
            else:
                floor = extent * floor_fraction
                height_pct = 100.0 * max(magnitude, floor if magnitude > 0 else 0.0) / extent
            points.append(TrendPoint(
                label=label, value=value, is_now=(label == "Now"),
                is_distant=(label in _DISTANT_LABELS), direction=direction,
                height_pct=round(min(height_pct, 100.0), 1),
            ))
    else:
        span = max_value - min_value
        if span > 0:
            floor_baseline = min_value - span * floor_fraction
        else:
            floor_baseline = min_value - abs(min_value) * floor_fraction if min_value else -1.0
        denom = max_value - floor_baseline
        for label, value in zip(labels, raw_values):
            height_pct = 100.0 if denom <= 0 else 100.0 * (value - floor_baseline) / denom
            points.append(TrendPoint(
                label=label, value=value, is_now=(label == "Now"),
                is_distant=(label in _DISTANT_LABELS), direction="up",
                height_pct=round(max(0.0, min(100.0, height_pct)), 1),
            ))

    return TrendChart(points=points, bidirectional=bidirectional, has_data=has_data)


_RANGE_MONTHS = {"1m": 1, "3m": 3, "6m": 6, "9m": 9, "12m": 12}
_VALID_RANGES = set(_RANGE_MONTHS) | {"ytd"}


def _last_n_months_including_current(today: date, n: int) -> list[str]:
    periods = []
    year, month = today.year, today.month
    for _ in range(n):
        periods.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return list(reversed(periods))


@dataclass
class CashFlowMonth:
    label: str
    income: float
    expense: float
    income_by_day: Optional[float] = None     # the month up to the same day of the month as today (step 10a)
    expense_by_day: Optional[float] = None
    current: bool = False


@dataclass
class CashFlowChart:
    months: list[CashFlowMonth] = field(default_factory=list)
    max_value: float = 0.0
    range_key: str = "12m"


def build_cash_flow_chart(
    monthly_income: dict[str, float],
    monthly_expense: dict[str, float],
    range_key: str,
    today: date,
    income_by_day: Optional[dict[str, float]] = None,
    expense_by_day: Optional[dict[str, float]] = None,
) -> CashFlowChart:
    range_key = range_key if range_key in _VALID_RANGES else "12m"
    if range_key == "ytd":
        periods = [f"{today.year:04d}-{m:02d}" for m in range(1, today.month + 1)]
    else:
        periods = _last_n_months_including_current(today, _RANGE_MONTHS[range_key])

    current = f"{today.year:04d}-{today.month:02d}"
    months = [
        CashFlowMonth(label=p, income=monthly_income.get(p, 0.0), expense=monthly_expense.get(p, 0.0),
                      income_by_day=(income_by_day or {}).get(p, 0.0) if income_by_day is not None else None,
                      expense_by_day=(expense_by_day or {}).get(p, 0.0) if expense_by_day is not None else None,
                      current=p == current)
        for p in periods
    ]
    max_value = max([m.income for m in months] + [m.expense for m in months] + [0.0])
    return CashFlowChart(months=months, max_value=max_value, range_key=range_key)


# --- the chart rule's month data (PLAN step 10a) -----------------------------------------------------------------

def same_day(period: str, today: date) -> date:
    """The day of `period` ('YYYY-MM') with today's day of the month (its last day when the month is shorter)."""
    import calendar
    year, month = (int(p) for p in period.split("-"))
    return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))


def trend_items(full_complete: dict[str, float], by_day_complete: dict[str, float], today: date, mtd_value: float):
    """The trend card's columns for the one chart component: Now (this month so far, nothing past today) and the
    averages over the last 1, 3, 6, 12, 18 and 24 complete months, each a full month (tinted) with the same months'
    value by today's day of the month (solid). The same values as `build_trend_chart`, plus the like-for-like part."""
    from app.ui.charts import Item
    items = [Item("Now", round(mtd_value, 2), current=True)]
    for n in _HORIZONS:
        periods = [p for p in _complete_months_before(today, n) if p in full_complete]
        if not periods:
            continue
        full = sum(full_complete[p] for p in periods) / len(periods)
        by = sum(by_day_complete.get(p, 0.0) for p in periods) / len(periods)
        items.append(Item(_HORIZON_LABELS[n], round(by, 2), full=round(full, 2)))
    return items


def cash_flow_items(chart: "CashFlowChart", which: str):
    """One side of the cash flow (income or expense) as columns: each past month full with its same-day part, the
    current month so far only."""
    from app.ui.charts import Item
    out = []
    for m in chart.months:
        full = getattr(m, which)
        by = getattr(m, f"{which}_by_day")
        import calendar
        label, sub = m.label, ""
        if len(m.label) == 7:
            label = calendar.month_abbr[int(m.label[5:7])]
            sub = m.label[:4] if (not out or m.label.endswith("-01")) else ""
        if m.current:
            out.append(Item(label, round(full, 2), current=True, sub=sub))
        else:
            out.append(Item(label, round(by if by is not None else full, 2), full=round(full, 2), sub=sub))
    return out
