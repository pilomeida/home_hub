"""The one Jinja2 environment every router renders with. Registry-derived
globals live here so templates (the nav, domain badges) never hard-code a
domain."""

from typing import Optional

from fastapi.templating import Jinja2Templates

from app.domains.registry import get_spec, implemented_domains, is_implemented
from app.models.domain import Domain

templates = Jinja2Templates(directory="app/templates")


def domain_label(domain: Optional[Domain]) -> str:
    if domain is None:
        return ""
    if is_implemented(domain):
        return get_spec(domain).label
    return domain.value.replace("_", " ").title()


templates.env.globals["domain_nav"] = implemented_domains
templates.env.globals["domain_label"] = domain_label