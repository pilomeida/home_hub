"""One-off (2026-10-19): open the informal loans Pedro named, from the bank lines since the baseline.
Dry run unless --apply (a dry run writes nothing).

  Matias Correia de Almeida (we lent): advances = his lines already filed "money lent to family" + the Wamos
      tranches; repayments = every transfer from him; the opening balance on 2025-10-09 is worked out backwards
      so that today's balance is 0. Everything else with him stays ordinary family spending.
  Eduardo Manuel Coelho (we owe): his transfers to us are advances, ours to him repayments; lines under EUR 100
      are left out. The balance before 2025-10-10 is not known.
  Emanuel Correia (we owe): opening EUR 15,000 from before the bank data plus the EUR 2,000 he sent on
      2026-08-13 = EUR 17,000. His other transfers are payments made on his behalf, repaid: left as they are.

    python scripts/create_informal_loans.py [--apply]
"""
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.baseline import BASELINE_DATE  # noqa: E402
from app.db import engine  # noqa: E402
from app.models.category_node import CategoryNode  # noqa: E402
from app.models.debt import Debt, DebtDirection  # noqa: E402
from app.models.person import Person  # noqa: E402
from app.models.transaction import Transaction, TransactionType  # noqa: E402
from app.services.debt_ledger import add_entry, create_informal_debt, link_transaction_as_entry, recompute  # noqa: E402
from app.services.loan_math import informal_summary  # noqa: E402
from app.services.taxonomy import file_transaction, get_node  # noqa: E402

BEFORE_BASELINE = date(2025, 10, 9)


def _rows(session, text_like, *, kinds=None, min_amount=0.0):
    stmt = select(Transaction).where(Transaction.after_baseline(), Transaction.settled_by_id.is_(None),
                                     Transaction.debt_id.is_(None), Transaction.description.ilike(text_like))
    rows = [t for t in session.exec(stmt).all() if round(t.amount, 2) >= min_amount]
    return sorted([t for t in rows if kinds is None or t.transaction_type in kinds],
                  key=lambda t: (t.paid_date or t.due_date, t.id))


def _slug(session, t):
    node = session.get(CategoryNode, t.category_id) if t.category_id else None
    return node.slug if node else None


def _link(session, debt, rows, kind_of, family=False):
    for t in rows:
        kind = kind_of(t)
        link_transaction_as_entry(session, debt, t, kind)
        if family:     # Matias is family: keep the family nodes, not the friends ones the ledger defaults to
            file_transaction(session, t, get_node(
                session, "loans-debt.money-lent-out.to-family" if kind == "advance" else "loans-debt.money-lent-out.from-family"))


def build(session: Session) -> list[tuple[str, Debt]]:
    out = []
    # --- Matias ---
    matias = [t for t in _rows(session, "%MATIAS%", kinds=[TransactionType.DEBIT])
              if (_slug(session, t) or "").endswith("money-lent-out.to-family")]
    wamos = _rows(session, "%12092-%", kinds=[TransactionType.DEBIT])
    credits = _rows(session, "%MATIAS%", kinds=[TransactionType.CREDIT])
    advances = matias + wamos
    gap = Decimal(str(round(sum(t.amount for t in credits) - sum(t.amount for t in advances), 2)))
    debt = create_informal_debt(session, "Matias Correia de Almeida", DebtDirection.OWED_TO_US)
    if gap > 0:
        add_entry(session, debt, "adjust_up", gap, BEFORE_BASELINE,
                  note="opening balance on 2025-10-09, worked out backwards so that the balance is 0 today")
    _link(session, debt, advances, lambda t: "advance", family=True)
    _link(session, debt, credits, lambda t: "repayment", family=True)
    out.append(("Matias Correia de Almeida", debt))
    # --- Eduardo ---
    rows = [t for pattern in ("%EDUARDO M COELHO%", "%EDUARDO COELHO%", "%EDUARDO MANUEL DA S%")
            for t in _rows(session, pattern, min_amount=100.0)]
    rows = list({t.id: t for t in rows}.values())
    debt = create_informal_debt(session, "Eduardo Manuel Coelho", DebtDirection.OWED_BY_US)
    _link(session, debt, sorted(rows, key=lambda t: (t.paid_date or t.due_date, t.id)),
          lambda t: "advance" if t.transaction_type == TransactionType.CREDIT else "repayment")
    out.append(("Eduardo Manuel Coelho", debt))
    # --- Emanuel ---
    debt = create_informal_debt(session, "Emanuel Correia", DebtDirection.OWED_BY_US)
    add_entry(session, debt, "adjust_up", 15000, BEFORE_BASELINE, note="opening balance from before the bank data (Pedro)")
    big = session.get(Transaction, 5175)
    if big is None or round(big.amount, 2) != 2000.0 or "EMANUEL CORREIA" not in big.description.upper():
        raise SystemExit("Line 5175 (EUR 2,000 from Emanuel Correia on 2026-08-13) is not what was expected; stopping.")
    link_transaction_as_entry(session, debt, big, "advance")
    out.append(("Emanuel Correia", debt))
    for _, d in out:
        recompute(session, d)
    return out


def main() -> None:
    apply = "--apply" in sys.argv
    with Session(engine) as session:
        for name in ("Matias Correia de Almeida", "Eduardo Manuel Coelho", "Emanuel Correia"):
            if session.exec(select(Person).where(Person.name == name)).first() is not None:
                raise SystemExit(f"{name} already has a person record; this one-off ran before. Stopping.")
        made = build(session)
        for name, debt in made:
            s = informal_summary(session, debt)
            print(f"{name}: {debt.direction.value}; advances {s.advances_total}, repayments {s.repayments_total}, "
                  f"adjustments {s.adjustments_net}, balance {s.balance}, overpaid {s.overpaid_by}, "
                  f"{len(s.entries)} lines")
            for e, running in s.entries:
                print(f"   {e.entry_date}  {e.kind:<11} {float(e.amount):>9.2f}  running {running}  txn {e.transaction_id or '-'}  {e.note or ''}")
        if apply:
            session.commit()
            print("Saved.")
        else:
            session.rollback()
            print("Dry run; nothing saved. Add --apply.")


if __name__ == "__main__":
    main()
