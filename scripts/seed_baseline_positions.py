"""Record the opening position on the baseline day (10 Oct 2025, app/baseline.py): bank balances, loan
capital, fund holdings. Figures were confirmed by Pedro on 2026-10-17 from the bank statements. Safe to run
twice (replaces the rows). Dry run unless --apply.

    python scripts/seed_baseline_positions.py [--apply]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.baseline import BASELINE_DATE  # noqa: E402
from app.db import engine  # noqa: E402
from app.models.baseline import BaselinePosition  # noqa: E402

# (kind, label, amount, account_id, debt_id, source)
POSITIONS = [
    ("cash", "Santander current account", 13746.14, 1, None,
     "bank running balance on 10 Oct 2025 (13,853.49 at close) minus that day's own movements"),
    ("cash", "Revolut current account", 29.87, 2, None, "Revolut statement Sep-Nov 2025: balance after 8 Oct 2025, nothing on 9 Oct"),
    ("loan", "Mafra house – acquisition", 101992.28, None, 1, "capital left after the 2 Oct 2025 instalment (Santander statement)"),
    ("loan", "Mafra house – refurbishing", 116880.29, None, 2, "capital left after the 2 Oct 2025 instalment (Santander statement)"),
    ("loan", "Moscavide house", 24226.53, None, 3, "capital left after the 2 Oct 2025 instalment (loan schedule)"),
    ("fund", "Santander Aforro PPR (Pedro)", 824.10, None, None, "155.682025 units at the 1 Oct 2025 price 5.2935; no movement until 31 Oct"),
    ("fund", "Santander Aforro PPR (Rute)", 824.10, None, None, "same as Pedro's: 155.682025 units at 5.2935"),
]


def main() -> None:
    apply = "--apply" in sys.argv
    with Session(engine) as session:
        old = session.exec(select(BaselinePosition)).all()
        print(f"{len(old)} existing row(s) will be replaced; {len(POSITIONS)} written.")
        for kind, label, amount, *_ in POSITIONS:
            print(f"  {kind:5} {label:34} {amount:>12,.2f}")
        if not apply:
            print("Dry run. Add --apply to write.")
            return
        for row in old:
            session.delete(row)
        for kind, label, amount, account_id, debt_id, source in POSITIONS:
            session.add(BaselinePosition(kind=kind, label=label, amount=amount, as_of=BASELINE_DATE,
                                         account_id=account_id, debt_id=debt_id, source=source))
        session.commit()
        print("Written.")


if __name__ == "__main__":
    main()
