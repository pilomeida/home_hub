"""Other papers' card on the '/' Overview."""

from datetime import date

from sqlmodel import Session

from app.categories.base import CardLine, CategoryCard
from app.categories.entries import category_entries
from app.models.category import Category


def other_overview_card(session: Session, today: date) -> CategoryCard:
    n = len(category_entries(session, Category.OTHER))
    return CategoryCard(label="Other papers", url="/other",
                        lines=[CardLine(text=f"{n} paper{'s' if n != 1 else ''} on file", url="/other")])
