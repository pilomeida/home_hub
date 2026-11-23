"""What an added area or an added kind is, derived from its paper_types rows (PLAN step 12a, S3: derived, not
written per area). An added area is a CategorySpec with the generic fields, the generic handler and the shared paper
pages at /areas/<value>; a kind added to a built-in area joins that area's spec, read by the generic reader."""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Iterable, Optional

from sqlmodel import Session

from app.categories.base import (
    CardLine, CategoryCard, CategoryHandler, CategorySpec, FieldKind, FieldSpec, KindSpec, NavLink, dated_by_fields,
)
from app.categories.generic.handler import GenericPaperHandler
from app.models.category import Category
from app.models.document import Document
from app.models.paper_type import PaperType
from app.privacy.tiers import Tier

AREAS_PREFIX = "/areas"

# The generic details (the old Other papers' fields): read from the paper when left empty.
GENERIC_FIELDS = (
    FieldSpec("title", "Title", FieldKind.TEXT, help="Read from the paper when left empty."),
    FieldSpec("paper_date", "Date of the paper", FieldKind.DATE),
    FieldSpec("about", "Who or what it is about", FieldKind.SUGGEST, help="A person, the house, a car, an account."),
    FieldSpec("summary", "Summary", FieldKind.LONGTEXT, help="Read from the paper when left empty."),
)

_generic = GenericPaperHandler()


async def _read_generic(session: Session, document: Document) -> Document:
    return await _generic.process(session, document)


def added_kind(row: PaperType) -> KindSpec:
    """An added kind: suggested Mid on the Privacy page (the cautious middle; High is only ever Pedro's choice)."""
    return KindSpec(row.kind, row.label, row.description or f"A paper of the kind “{row.label}”.",
                    suggested_tier=Tier.MID, reader=_read_generic, dated_by=dated_by_fields("paper_date"))


def _overview(category: Category, label: str):
    def card(session: Session, today: date) -> CategoryCard:
        from app.categories.entries import category_entries
        count = sum(1 for e in category_entries(session, category) if e.document is not None)
        return CategoryCard(label=label, url=f"{AREAS_PREFIX}/{category.value}",
                            lines=[CardLine(text=f"{count} paper{'s' if count != 1 else ''}",
                                            url=f"{AREAS_PREFIX}/{category.value}")])
    return card


def added_area(area: PaperType, kinds: Iterable[PaperType]) -> CategorySpec:
    category = Category.trusted(area.area)
    url = f"{AREAS_PREFIX}/{category.value}"
    return CategorySpec(
        category=category, label=area.label,
        description=area.description or f"Papers about {area.label.lower()}.",
        home_url=url, kinds=tuple(added_kind(k) for k in kinds), fields=GENERIC_FIELDS, handler=_generic,
        nav_links=(NavLink(area.label, url),), overview_card=_overview(category, area.label),
        document_url=lambda document, url=url: f"{url}/documents/{document.id}",
        dated_by=dated_by_fields("paper_date"),
    )


class _WithAddedKinds(CategoryHandler):
    """A built-in area's handler, with its added kinds read by the generic reader; everything else is the area's."""

    def __init__(self, base: CategoryHandler, added: frozenset[str]):
        self.base, self.added = base, added

    def _for(self, kind: Optional[str]) -> CategoryHandler:
        return _generic if kind in self.added else self.base

    async def process(self, session, document):
        return await self._for(document.paper_kind).process(session, document)

    async def on_fields_changed(self, session, document, previous_fields):
        return await self._for(document.paper_kind).on_fields_changed(session, document, previous_fields)

    async def process_record(self, session, record):
        return await self.base.process_record(session, record)

    async def on_record_changed(self, session, record, previous_fields):
        return await self.base.on_record_changed(session, record, previous_fields)

    def refile_blocker(self, session, document):
        return self._for(document.paper_kind).refile_blocker(session, document)

    async def withdraw(self, session, document):
        return await self._for(document.paper_kind).withdraw(session, document)

    def __getattr__(self, name):              # an area's own extras (e.g. a helper a router calls) stay reachable
        return getattr(self.base, name)


def with_added_kinds(spec: CategorySpec, rows: list[PaperType]) -> CategorySpec:
    """A built-in area's spec with kinds Pedro added. The generic fields apply to those kinds; a field the area
    already has under the same key is widened to them instead of doubled."""
    if not rows:
        return spec
    kinds = tuple(added_kind(r) for r in rows)
    values = frozenset(k.value for k in kinds)
    fields = list(spec.fields)
    for generic in GENERIC_FIELDS:
        at = next((i for i, f in enumerate(fields) if f.key == generic.key), None)
        if at is None:
            fields.append(dataclasses.replace(generic, kinds=values))
        elif fields[at].kinds is not None:
            fields[at] = dataclasses.replace(fields[at], kinds=fields[at].kinds | values)
    return dataclasses.replace(spec, kinds=spec.kinds + kinds, fields=tuple(fields),
                               handler=_WithAddedKinds(spec.handler, values))
