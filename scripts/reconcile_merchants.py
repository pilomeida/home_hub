"""One category per merchant. Dry run unless --apply. Back up the DB first (docs/SYSADMIN.md).

Usage:
    python scripts/reconcile_merchants.py                      # dry run, prints the report
    python scripts/reconcile_merchants.py --apply [--log FILE] # writes; FILE gets every move as JSON, to reverse it
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.services.merchant_reconcile import reconcile_merchants  # noqa: E402
from app.services.taxonomy import ensure_taxonomy  # noqa: E402


def main() -> None:
    apply = "--apply" in sys.argv
    log = sys.argv[sys.argv.index("--log") + 1] if "--log" in sys.argv else None
    with Session(engine) as session:
        ensure_taxonomy(session)
        report = reconcile_merchants(session, dry_run=not apply)
    print({k: v for k, v in vars(report).items() if not isinstance(v, list)}, flush=True)
    if log:
        Path(log).write_text(json.dumps(
            {"moves": report.moves, "merchant_changes": report.merchant_changes}))


if __name__ == "__main__":
    main()
