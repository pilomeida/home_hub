"""Give loan instalments and loan-insurance debits their one fixed merchant. Dry run unless --apply.
Only the merchant link changes (never a category or a loan link). Back up the DB first.

    python scripts/relabel_structured_merchants.py
    python scripts/relabel_structured_merchants.py --apply [--log FILE]   # FILE: every (txn, old, new) as JSON
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.services.merchant_relabel import relabel_structured  # noqa: E402
from app.services.taxonomy import ensure_taxonomy  # noqa: E402


def main() -> None:
    apply = "--apply" in sys.argv
    log = sys.argv[sys.argv.index("--log") + 1] if "--log" in sys.argv else None
    with Session(engine) as session:
        ensure_taxonomy(session)
        report = relabel_structured(session, dry_run=not apply)
    print({k: v for k, v in vars(report).items() if not isinstance(v, list)}, flush=True)
    if log:
        Path(log).write_text(json.dumps(report.moves))


if __name__ == "__main__":
    main()
