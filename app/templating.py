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

# Shared presentation filters (app/services/presentation.py) -- the one
# place a technical failure reason, an internal claim key, or a raw
# datetime is turned into something a person can read.
templates.env.filters["friendly_reason"] = friendly_reason
templates.env.filters["lisbon"] = lisbon_datetime
templates.env.filters["humanize_key"] = humanize_key
templates.env.globals["claim_label"] = claim_label
templates.env.filters["display_date"] = display_date
templates.env.filters["display_datetime"] = display_datetime