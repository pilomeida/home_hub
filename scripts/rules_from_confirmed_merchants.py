"""One-off (2026-11-19, PLAN step 2b): a merchant a person already confirmed gets the person's rule a confirmation
writes today (counterparty -> spending leaf), so no batch pass can override it. Only `rules` is written; no entry
is re-filed (each rule equals the merchant's current default).

Run on the VPS, from the app folder, as the app's user:
    python scripts/rules_from_confirmed_merchants.py            # dry run: how many rules it would write
    python scripts/rules_from_confirmed_merchants.py --apply
Safe to run again."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine  # noqa: E402
from app.models.category_node import CategoryNode  # noqa: E402
from app.models.merchant import Merchant  # noqa: E402
from app.models.rule import COUNTERPARTY, PERSON, Rule  # noqa: E402
from app.services import rules  # noqa: E402


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    with Session(engine) as session:
        merchants = session.exec(select(Merchant).where(Merchant.confirmed == True, Merchant.merged_into_id.is_(None),  # noqa: E712
                                                        Merchant.default_category_id.is_not(None))).all()
        made, skipped, already = 0, 0, 0
        for m in merchants:
            node = session.get(CategoryNode, m.default_category_id)
            existing = session.exec(select(Rule).where(Rule.looks_at == COUNTERPARTY, Rule.source == PERSON,
                                                       Rule.pattern == (m.canonical_name or "").strip().lower())).first()
            if existing is not None:
                already += 1
                continue
            if node is None or node.kind == "in" or node.slug.startswith("unsorted"):
                skipped += 1
                continue
            made += 1
            if apply:
                rule = rules.remember_merchant(session, m, node, by="moved from confirmed merchants")
                rule.note = "A person confirmed this merchant before rules existed (moved 2026-11-19)"
        print(f"{len(merchants)} confirmed merchants: {made} rules {'written' if apply else 'to write'}, "
              f"{already} already have one, {skipped} skipped (money in, or no real category)")
        if apply:
            session.commit()
        else:
            print("dry run: nothing changed (add --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
