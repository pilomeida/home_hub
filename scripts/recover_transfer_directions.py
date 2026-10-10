"""Give entries typed 'transfer' a direction from their description (de = from, p/ or para = to). Dry run unless --apply.
    python scripts/recover_transfer_directions.py [--apply] [--log FILE]
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.services.transfer_direction import recover_transfer_directions  # noqa: E402


def main() -> None:
    apply = "--apply" in sys.argv
    log = sys.argv[sys.argv.index("--log") + 1] if "--log" in sys.argv else None
    with Session(engine) as session:
        report = recover_transfer_directions(session, dry_run=not apply)
    print({"to_debit": report.to_debit, "to_credit": report.to_credit, "unclear": report.unclear, "applied": apply})
    if log:
        Path(log).write_text(json.dumps(report.moves))


if __name__ == "__main__":
    main()
