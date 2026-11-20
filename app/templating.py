"""The one Jinja2 environment every router renders with. Registry-derived
globals live here so templates (the nav, domain badges) never hard-code a
domain."""

from typing import Optional

from fastapi.templating import Jinja2Templates

from app.categories.registry import get_spec, implemented_categories, is_implemented
from app.models.category import Category
from app.privacy.notices import HEALTH_NOTICE, SCAN_NOTICE
from app.services.presentation import (
    claim_label, display_date, display_datetime, friendly_reason, humanize_key, lisbon_datetime,
)

templates = Jinja2Templates(directory="app/templates")


def category_label(category: Optional[Category]) -> str:
    if category is None:
        return ""
    if is_implemented(category):
        return get_spec(category).label
    return category.value.replace("_", " ").title()


templates.env.globals["category_nav"] = implemented_categories
templates.env.globals["category_label"] = category_label
templates.env.globals["scan_notice"] = SCAN_NOTICE
templates.env.globals["health_notice"] = HEALTH_NOTICE
from app.problems import open_count as _open_problem_count  # noqa: E402
templates.env.globals["open_problem_count"] = _open_problem_count
from app.services.to_check import menu_count as _to_check_count  # noqa: E402
templates.env.globals["to_check_count"] = _to_check_count
from app.services.review import menu_count as _waiting_count  # noqa: E402
templates.env.globals["waiting_count"] = _waiting_count

# Shared presentation filters (app/services/presentation.py) -- the one
# place a technical failure reason, an internal claim key, or a raw
# datetime is turned into something a person can read.
templates.env.filters["friendly_reason"] = friendly_reason
templates.env.filters["lisbon"] = lisbon_datetime
templates.env.filters["humanize_key"] = humanize_key
templates.env.globals["claim_label"] = claim_label
from app.ui import charts as _charts  # noqa: E402
templates.env.globals["charts"] = _charts       # the one chart component (PLAN step 10a)


def _category_bars(rows):
    return [_charts.Bar(r.category, r.current_value, getattr(r, "last_month_value", 0.0), r.drill_down_url) for r in rows]


def _day_of_month() -> int:
    from datetime import date
    return date.today().day


from app.services.overview_charts import cash_flow_items as _cash_flow_items  # noqa: E402
templates.env.globals["charts_items"] = _cash_flow_items
templates.env.globals["category_bars"] = _category_bars
templates.env.globals["day_of_month"] = _day_of_month
templates.env.filters["display_date"] = display_date          # the one date format (yyyy-mm-dd)
templates.env.filters["display_datetime"] = display_datetime


def money(value, whole: bool = False) -> str:
    """The one way a euro amount is shown: €1,234.56, or €1,235 with whole=True (PLAN step 9, D4)."""
    return f"€{value:,.0f}" if whole else f"€{value:,.2f}"


templates.env.filters["money"] = money


def _utility_items(rows):
    """A utility series' rows (period label, value or None) as columns; the periods are whole bills, none current."""
    return [_charts.Item(str(label), float(value)) for label, value in rows if value is not None]


def _utility_format(unit: str = "", precision: int = 2):
    unit = unit or ""

    def fmt(v):
        return f"{v:,.{precision}f}{unit}"
    return fmt


templates.env.globals["utility_items"] = _utility_items
templates.env.globals["utility_format"] = _utility_format

from app.ui import shell as _shell  # noqa: E402
templates.env.globals["shell_sections"] = _shell.sections       # the menu and section rows (PLAN step 10b)
templates.env.globals["shell_active"] = _shell.active

from app.services.who import header as _who_header  # noqa: E402
templates.env.globals["who_header"] = _who_header        # the Who chip (PLAN step 10i)
