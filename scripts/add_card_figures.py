"""Add Pedro's card lines the sync never stored, and count Rute's card payments as spending (see
app/services/card_figures.py). Dry run unless --apply.

    python scripts/add_card_figures.py "<card list>.pdf" [--apply]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.services.card_figures import (  # noqa: E402
    add_lines, file_rute_card_payments, read_card_pdf, reconcile, redate_rows, rute_card_payments,
)
from app.services.taxonomy import ensure_taxonomy  # noqa: E402

CARD_ACCOUNT_ID = 3


def main() -> None:
    apply = "--apply" in sys.argv
    path = next(a for a in sys.argv[1:] if not a.startswith("--"))
    with Session(engine) as session:
        lines = read_card_pdf(path)
        todo, redates = reconcile(session, CARD_ACCOUNT_ID, lines)
        rute = rute_card_payments(session)
        print(f"{len(lines)} lines on the card list; {len(todo)} to add:")
        for l in sorted(todo, key=lambda l: l.day):
            print(f"  {l.day}  {l.amount:>9.2f}  {l.description}")
        print(f"{len(redates)} stored row(s) carry the day the sync fetched them, not the card's day:")
        for t, l in redates:
            print(f"  #{t.id} {t.description}  {t.amount:.2f}: {t.paid_date} -> {l.day}")
        print(f"{len(rute)} payments of Rute's card to count as spending ({sum(t.amount for t in rute):.2f}):")
        for t in rute:
            print(f"  {t.paid_date}  {t.amount:>9.2f}  {t.description}")
        if not apply:
            print("Dry run. Add --apply to write.")
            return
        ensure_taxonomy(session)
        session.commit()
        redate_rows(session, redates)
        print(f"Added {add_lines(session, CARD_ACCOUNT_ID, todo)}; filed {len(file_rute_card_payments(session))}.")


if __name__ == "__main__":
    main()
