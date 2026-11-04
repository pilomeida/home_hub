"""The domain registry: the single list of implemented domains.

Adding a domain = add its Category member (+ migration), create
app/categories/<domain>/spec.py exporting SPEC, and append the module path
below. Nothing else in shared code changes. Specs are imported lazily so
domain modules may import shared services without import cycles."""

from __future__ import annotations

import importlib
from typing import Optional

from app.categories.base import CategorySpec
from app.models.document import Document, DocumentStatus
from app.models.category import Category

_SPEC_MODULES: tuple[str, ...] = (
    "app.categories.financials.spec",
    "app.categories.house.spec",
    "app.categories.insurance.spec",
    "app.categories.health.spec",
)

_specs_cache: Optional[dict[Category, CategorySpec]] = None


class UnknownCategoryError(KeyError):
    pass


def _specs() -> dict[Category, CategorySpec]:
    global _specs_cache
    if _specs_cache is None:
        specs: dict[Category, CategorySpec] = {}
        for module_path in _SPEC_MODULES:
            spec: CategorySpec = importlib.import_module(module_path).SPEC
            if spec.category in specs:
                raise ValueError(f"Domain {spec.category} registered twice")
            specs[spec.category] = spec
        _specs_cache = specs
    return _specs_cache


def implemented_domains() -> list[CategorySpec]:
    return list(_specs().values())


def is_implemented(category: Optional[Category]) -> bool:
    return category is not None and category in _specs()


def get_spec(category: Optional[Category]) -> CategorySpec:
    try:
        return _specs()[category]
    except KeyError:
        raise UnknownCategoryError(category) from None


def document_url(document: Document) -> Optional[str]:
    """Where a document is viewed: its domain's page once finalized; its
    Inbox card while it waits for review; None otherwise (e.g. discarded)."""
    if is_implemented(document.category):
        return get_spec(document.category).document_url(document)
    if document.category is None and document.status == DocumentStatus.PENDING_REVIEW:
        return f"/inbox#inbox-{document.id}"
    return None


def record_url(record) -> Optional[str]:
    if not is_implemented(record.category):
        return None
    spec = get_spec(record.category)
    return spec.record_url(record) if spec.record_url else None