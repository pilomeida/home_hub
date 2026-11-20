"""Vehicles' card on the Overview: the cars and the next date that needs a person."""

from datetime import date

from sqlmodel import Session

from app.categories.base import CardLine, CategoryCard
from app.categories.vehicles.garage import cars, next_date


def vehicles_overview_card(session: Session, today: date) -> CategoryCard:
    all_cars = cars(session, today)
    lines = [CardLine(text=f"{len(all_cars)} car{'s' if len(all_cars) != 1 else ''}", url="/vehicles")]
    late = [(c, d) for c in all_cars for d in c.dates if d.late]
    for c, d in late:
        lines.append(CardLine(text=f"{d.label} for {c.plate} overdue since {d.when.isoformat()}", url="/vehicles",
                              attention=True))
    nxt = next_date(session, today)
    if nxt is not None:
        c, d = nxt
        lines.append(CardLine(text=f"next: {d.label.lower()} for {c.plate} on {d.when.isoformat()}", url="/vehicles",
                              attention=d.soon))
    return CategoryCard(label="Vehicles", url="/vehicles", lines=lines)
