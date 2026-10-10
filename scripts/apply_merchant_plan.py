"""Apply a reviewed merchant plan (JSON). Writes a full BEFORE snapshot first, so everything can be reversed.
Dry run unless --apply.

    python scripts/apply_merchant_plan.py plan.json [--apply] [--snapshot FILE]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine  # noqa: E402
from app.models.merchant import Merchant  # noqa: E402
from app.models.transaction import Transaction  # noqa: E402
from app.services.merchant_plan import apply_plan  # noqa: E402
from app.services.taxonomy import ensure_taxonomy  # noqa: E402


def main() -> None:
    plan = json.loads(Path(sys.argv[1]).read_text())
    apply = "--apply" in sys.argv
    snap = sys.argv[sys.argv.index("--snapshot") + 1] if "--snapshot" in sys.argv else None
    with Session(engine) as session:
        ensure_taxonomy(session)
        if apply and snap:
            Path(snap).write_text(json.dumps({
                "transactions": [[t.id, t.merchant_id, t.category_id, t.transaction_type.value] for t in session.exec(select(Transaction)).all()],
                "merchants": [[m.id, m.canonical_name, m.merged_into_id, m.default_category_id, m.default_credit_category_id, m.by_provider]
                              for m in session.exec(select(Merchant)).all()]}))
        report = apply_plan(session, plan, dry_run=not apply)
    print({k: v for k, v in vars(report).items() if k != "survivors"}, "applied" if apply else "dry run")


if __name__ == "__main__":
    main()
