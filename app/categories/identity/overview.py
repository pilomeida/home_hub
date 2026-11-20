"""Identity & Legal's card on the Overview: how many papers and the next renewal date, never a name or number."""

from datetime import date

from sqlmodel import Session

from app.categories.base import CardLine, CategoryCard
from app.categories.identity.renewals import next_expiry, papers_by_holder


def identity_overview_card(session: Session, today: date) -> CategoryCard:
    groups = papers_by_holder(session, today)
    n = sum(len(papers) for _h, papers in groups)
    lines = [CardLine(text=f"{n} paper{'s' if n != 1 else ''} on file", url="/identity")]
    expired = sum(1 for _h, papers in groups for p in papers if p.late)
    if expired:
        lines.append(CardLine(text=f"{expired} expired", url="/identity", attention=True))
    nxt = next_expiry(session, today)
    if nxt is not None:
        lines.append(CardLine(text=f"next expiry {nxt.expires_on.isoformat()}", url="/identity", attention=nxt.soon))
    return CategoryCard(label="Identity & Legal", url="/identity", lines=lines)
