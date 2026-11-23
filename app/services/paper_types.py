"""Adding an area or a kind of paper (PLAN step 12a): every paper has a type, and when none fits, the approval that
files it creates one (Pedro, 2026-11-23: no "Other", one paper of its type is fine). The rows live in `paper_types`;
the registry turns them into specs (app/categories/generic/spec.py)."""

from __future__ import annotations

import re
from typing import Optional

from sqlmodel import Session, select

from app.categories import registry
from app.models.category import Category
from app.models.paper_type import PaperType
from app.services.names import fold

NEW = "__new__"           # the Inbox's "+ New area" / "+ New kind of paper" choice


class AlreadyThere(ValueError):
    """The name is already an area, or already a kind of that area."""


def value_for(label: str) -> str:
    """The stored value for a typed name: no accents (ç → c, ã → a), lower case, words joined by "_"."""
    words = re.findall(r"[a-z0-9]+", fold(label))
    value = "_".join(words)[:40].strip("_")
    if value and value[0].isdigit():
        value = "n_" + value
    return value


def _clean(label: str) -> str:
    return " ".join((label or "").split())[:80]


def _area_taken(value: str, label: str) -> bool:
    for spec in registry.implemented_categories():
        if spec.category.value == value or value_for(spec.label) == value:
            return True
    return False


def add_kind(session: Session, category: Category, label: str, description: str = "", *, by: Optional[str],
             document_id: Optional[int] = None) -> PaperType:
    label = _clean(label)
    value = value_for(label)
    if not value:
        raise ValueError("A new kind of paper needs a name")
    spec = registry.get_spec(category)
    if any(k.value == value or value_for(k.label) == value for k in spec.kinds):
        raise AlreadyThere(f"{spec.label} already has “{label}”")
    row = PaperType(area=category.value, kind=value, label=label, description=_clean(description),
                    created_from_document_id=document_id, created_by=by)
    session.add(row)
    session.commit()
    registry.forget_types()
    return row


def add_area(session: Session, label: str, description: str = "", *, first_kind: str, by: Optional[str],
             document_id: Optional[int] = None) -> PaperType:
    """An area always comes with its first kind (a paper is filed as an area AND a kind)."""
    label, kind_label = _clean(label), _clean(first_kind)
    value, kind_value = value_for(label), value_for(kind_label)
    if not value:
        raise ValueError("A new area needs a name")
    if not kind_value:
        raise ValueError("A new area needs the name of its first kind of paper")
    if _area_taken(value, label):
        raise AlreadyThere(f"“{label}” is already an area")
    area = PaperType(area=value, kind=None, label=label, description=_clean(description),
                     created_from_document_id=document_id, created_by=by)
    session.add(area)
    session.add(PaperType(area=value, kind=kind_value, label=kind_label, description="",
                          created_from_document_id=document_id, created_by=by))
    session.commit()
    registry.forget_types()
    return area


def take_back(session: Session, rows: list[PaperType]) -> None:
    """Remove types an approval created when that approval is then refused (nothing is filed under them yet)."""
    for row in rows:
        session.delete(session.get(PaperType, row.id))
    session.commit()
    registry.forget_types()

