"""Re-file all transactions into the category tree. Dry-run unless --apply.

Re-runnable: transactions already filed are skipped. On the VPS, back up the
DB first (docs/SYSADMIN.md).

Usage:
    python scripts/refile_to_taxonomy.py            # dry run, prints the report
    python scripts/refile_to_taxonomy.py --apply    # writes
    python scripts/refile_to_taxonomy.py --reclassify [--concurrency N]  # N 1..12, default 6; writes; one LLM call per merchant with Unsorted
                                                       # transactions (through the llmsel gateway)

    python scripts/refile_to_taxonomy.py --reclassify --best-guess   # second pass: no "unsure" answer allowed

Note: --reclassify only covers rows already in Unsorted, so run --apply first.
Confirmed merchants are never touched.
"""

import asyncio

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.services.taxonomy import ensure_taxonomy  # noqa: E402
from app.services.taxonomy_refile import parse_concurrency, reclassify_unsorted, refile_all  # noqa: E402


def main() -> None:
    apply = "--apply" in sys.argv
    with Session(engine) as session:
        ensure_taxonomy(session)
        if "--reclassify" in sys.argv:
            report = asyncio.run(reclassify_unsorted(
                session, concurrency=parse_concurrency(sys.argv), best_guess="--best-guess" in sys.argv,
                progress=lambda line: print(line, flush=True)))
            print(report, flush=True)
        else:
            print(refile_all(session, dry_run=not apply))


if __name__ == "__main__":
    main()
