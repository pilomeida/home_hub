"""The one Jinja2 environment every router renders with. Registry-derived
globals live here so templates (the nav, domain badges) never hard-code a
domain."""

from typing import Optional

from fastapi.templating import Jinja2Templates

from app.domains.registry import get_spec, implemented_domains, is_implemented
from app.models.domain import Domain
from app.services.presentation import (
    claim_label, display_date, display_datetime, friendly_reason, humanize_key, lisbon_datetime,
)

templates = Jinja2Templates(directory="app/templates")


def domain_label(domain: Optional[Domain]) -> str:
    if domain is None:
        return ""
    if is_implemented(domain):
        return get_spec(domain).label
    return domain.value.replace("_", " ").title()


templates.env.globals["domain_nav"] = implemented_domains
templates.env.globals["domain_label"] = domain_label

# Shared presentation filters (app/services/presentation.py) -- the one
# place a technical failure reason, an internal claim key, or a raw
# datetime is turned into something a person can read.
templates.env.filters["friendly_reason"] = friendly_reason
templates.env.filters["lisbon"] = lisbon_datetime
templates.env.filters["humanize_key"] = humanize_key
templates.env.globals["claim_label"] = claim_label
templates.env.filters["display_date"] = display_date
templates.env.filters["display_datetime"] = display_datetime