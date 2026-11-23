"""Category: which area of the household a paper or record belongs to (Money & Bills, House, Health, ...). In the
model's words this is a CATEGORY; see the glossary at the top of docs/ARCHITECTURE.md.

An open list (PLAN step 12a, 2026-11-23): the nine areas built into the code are the constants below; an area Pedro
approves in the Inbox is a row in `paper_types` (app/models/paper_type.py), known to the registry
(app/categories/registry.py). `Category("work")` is refused with ValueError until such an area exists, as the old
fixed Enum refused an unknown value. Values are stored by NAME ("FINANCIALS", "WORK") in the `domain` columns, as
before: no paper changes."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy.types import String, TypeDecorator

_VALUE = re.compile(r"[a-z][a-z0-9_]{0,39}")


class Category(str):
    """An area's value ("house"). Compares, hashes and serialises as that string; `.value` and `.name` as an Enum
    member did."""
    __slots__ = ()

    FINANCIALS: "Category"
    HOUSE: "Category"
    HEALTH: "Category"
    INSURANCE: "Category"
    IDENTITY: "Category"    # Identity & Legal (PLAN step 8a)
    VEHICLES: "Category"    # Vehicles (PLAN step 8c)
    EDUCATION: "Category"   # Education (PLAN step 8d)
    PETS: "Category"        # Pets (PLAN step 8e)

    def __new__(cls, value: Any) -> "Category":
        if isinstance(value, Category):
            return value
        text = str(value).strip().lower()
        if text in BUILT_IN:
            return BUILT_IN[text]
        if not _VALUE.fullmatch(text):
            raise ValueError(f"{value!r} is not an area")
        from app.categories.registry import is_added_area   # lazy: the registry imports this module
        if not is_added_area(text):
            raise ValueError(f"{value!r} is not an area")
        return str.__new__(cls, text)

    @classmethod
    def trusted(cls, value: str) -> "Category":
        """A value read back from the database or from the paper_types table: not checked against the registry."""
        text = value.strip().lower()
        return BUILT_IN.get(text) or str.__new__(cls, text)

    @property
    def value(self) -> str:
        return str.__str__(self)

    @property
    def name(self) -> str:
        return self.value.upper()

    def __repr__(self) -> str:
        return f"Category({self.value!r})"

    def __str__(self) -> str:
        return self.value

    def __reduce__(self):
        return (Category.trusted, (self.value,))

    @classmethod
    def __get_pydantic_core_schema__(cls, source, handler):
        from pydantic_core import core_schema
        return core_schema.no_info_plain_validator_function(
            cls, serialization=core_schema.plain_serializer_function_ser_schema(lambda c: str.__str__(c)))


BUILT_IN: dict[str, Category] = {}
for _name in ("financials", "house", "health", "insurance", "identity", "vehicles", "education", "pets"):
    BUILT_IN[_name] = str.__new__(Category, _name)
    setattr(Category, _name.upper(), BUILT_IN[_name])


class CategoryType(TypeDecorator):
    """The `domain` columns: a Category stored by its NAME, exactly as the old SQLAlchemy Enum column stored it."""
    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return Category.trusted(str.__str__(value) if isinstance(value, Category) else str(value)).name

    def process_result_value(self, value, dialect):
        return None if value is None else Category.trusted(value)
