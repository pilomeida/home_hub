"""The domain registry: the single list of areas, built in and added.

A built-in area = a Category constant, app/categories/<area>/spec.py exporting SPEC, and its module path below.
An added area or kind (PLAN step 12a) = a row in `paper_types`, approved in the Inbox: merged here with the built-in
specs (app/categories/generic/spec.py). Every process (web, Telegram bot, schedulers) re-checks the rows at most every
few seconds, so a type approved in one is known in all. Specs are imported lazily so domain modules may import shared
services without import cycles."""

from __future__ import annotations

import importlib
import time
from typing import Callable, Optional

from app.categories.base import CategorySpec
from app.models.document import Document, DocumentStatus
from app.models.category import Category

_SPEC_MODULES: tuple[str, ...] = (
    "app.categories.financials.spec",
    "app.categories.house.spec",
    "app.categories.insurance.spec",
    "app.categories.health.spec",
    "app.categories.identity.spec",
    "app.categories.vehicles.spec",
    "app.categories.education.spec",
    "app.categories.pets.spec",
)

_specs_cache: Optional[dict[Category, CategorySpec]] = None       # the built-in areas (a test may narrow it)

RECHECK_SECONDS = 3.0
# Opens a session on the database the added types live in; tests point it at their own engine (tests/conftest.py).
session_factory: Optional[Callable[[], object]] = None
_merged: Optional[tuple[tuple, dict[Category, CategorySpec]]] = None   # (stamp, specs)
_checked_at = 0.0


class UnknownCategoryError(KeyError):
    pass


def _built_in() -> dict[Category, CategorySpec]:
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


def _open_session():
    if session_factory is not None:
        return session_factory()
    from sqlmodel import Session
    from app.db import engine
    return Session(engine)


def _read_rows() -> tuple[tuple, list]:
    from sqlalchemy.exc import OperationalError
    from sqlmodel import select
    from app.models.paper_type import PaperType
    try:
        with _open_session() as session:
            rows = list(session.exec(select(PaperType).order_by(PaperType.id)).all())
            for row in rows:
                session.expunge(row)
    except OperationalError:            # no table yet (before the migration runs): only the built-in areas
        return (), []
    return (len(rows), rows[-1].id if rows else 0), rows


def forget_types() -> None:
    """Re-read the added types on the next lookup (after an approval adds one, or in a test)."""
    global _checked_at
    _checked_at = 0.0


def _specs() -> dict[Category, CategorySpec]:
    global _merged, _checked_at
    built_in = _built_in()
    now = time.monotonic()
    if _merged is not None and _merged[0][0] is built_in and now - _checked_at < RECHECK_SECONDS:
        return _merged[1]
    stamp, rows = _read_rows()
    _checked_at = now
    if _merged is not None and _merged[0][0] is built_in and _merged[0][1] == stamp:
        return _merged[1]
    from app.categories.generic.spec import added_area, with_added_kinds
    specs = dict(built_in)
    areas = [r for r in rows if r.kind is None]
    added_values = {r.area for r in areas}
    for spec in list(specs.values()):
        specs[spec.category] = with_added_kinds(spec, [r for r in rows if r.kind and r.area == spec.category.value])
    for area in areas:
        if Category.trusted(area.area) not in specs:
            specs[Category.trusted(area.area)] = added_area(area, [r for r in rows if r.kind and r.area == area.area])
    _merged = ((built_in, stamp), specs)
    _added_areas.clear()
    _added_areas.update(added_values)
    return specs


_added_areas: set[str] = set()


def is_added_area(value: str) -> bool:
    """True when `value` is an area Pedro added (Category's own check for a value that is not built in)."""
    _specs()
    return value in _added_areas


def implemented_categories() -> list[CategorySpec]:
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