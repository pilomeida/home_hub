"""The domain registry: the single list of implemented domains.

Adding a domain = add its Domain member (+ migration), create
app/domains/<domain>/spec.py exporting SPEC, and append the module path
below. Nothing else in shared code changes. Specs are imported lazily so
domain modules may import shared services without import cycles."""

from __future__ import annotations

import importlib
from typing import Optional

from app.domains.base import DomainSpec
from app.models.document import Document
from app.models.domain import Domain

_SPEC_MODULES: tuple[str, ...] = (
    "app.domains.financials.spec",
)

_specs_cache: Optional[dict[Domain, DomainSpec]] = None


class UnknownDomainError(KeyError):
    pass


def _specs() -> dict[Domain, DomainSpec]:
    global _specs_cache
    if _specs_cache is None:
        specs: dict[Domain, DomainSpec] = {}
        for module_path in _SPEC_MODULES:
            spec: DomainSpec = importlib.import_module(module_path).SPEC
            if spec.domain in specs:
                raise ValueError(f"Domain {spec.domain} registered twice")
            specs[spec.domain] = spec
        _specs_cache = specs
    return _specs_cache


def implemented_domains() -> list[DomainSpec]:
    return list(_specs().values())


def is_implemented(domain: Optional[Domain]) -> bool:
    return domain is not None and domain in _specs()


def get_spec(domain: Optional[Domain]) -> DomainSpec:
    try:
        return _specs()[domain]
    except KeyError:
        raise UnknownDomainError(domain) from None


def document_url(document: Document) -> Optional[str]:
    """Where a document is viewed. None while it is unfinalized (domain not
    set) -- Plan B extends this function (not its callers) to return the
    Inbox URL for such documents."""
    if not is_implemented(document.domain):
        return None
    return get_spec(document.domain).document_url(document)