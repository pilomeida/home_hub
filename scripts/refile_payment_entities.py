"""File every stored entry that pays a known Multibanco entity under that entity's merchant and category
(app/services/payment_entities.py). Entries a person has checked are left alone. Dry run unless --apply.

    python scripts/refile_payment_entities.py [--apply]
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.models.category_node import CategoryNode  # noqa: E402
from app.services.payment_entities import refile_by_entity  # noqa: E402
from app.services.taxonomy import ensure_taxonomy  # noqa: E402


def main() -> None:
    apply = "--apply" in sys.argv
    with Session(engine) as session:
        ensure_taxonomy(session)      # adds any seed category missing yet (a dry run needs them to name the target)
        if apply:
            session.commit()
        changed = refile_by_entity(session, dry_run=not apply)
        counts = Counter((e.number, e.name, session.get(CategoryNode, t.category_id).slug if t.category_id else "(none)",
                          e.node_slug) for t, e in changed)
        for (number, name, old, new), n in sorted(counts.items()):
            print(f"{n:>3}  {number} {name}: {old} -> {new}")
        if not apply:
            session.rollback()
        print(f"{len(changed)} entries {'refiled' if apply else 'would change (dry run; add --apply)'}.")


if __name__ == "__main__":
    main()
