"""Re-read the Health benchmark papers with the real model and compare with their confirmed readings.

Run on the VPS (it needs the gateway), from the app folder, as the app's user:
    python scripts/health_benchmark.py run [--case NAME]      # exit code 1 if any case fails
    python scripts/health_benchmark.py promote DOCUMENT_ID NAME    # make a paper in the Hub a benchmark case
The papers live in data/health_benchmark/ (git-ignored: real health papers). Every reading goes out at house_high."""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.domains.health import benchmark  # noqa: E402
from app.domains.health.reading import read_health_document  # noqa: E402


async def _run(only):
    cases = benchmark.load_cases(only=only)
    if not cases:
        print("No benchmark cases (data/health_benchmark/<case>/paper.* + expected.json).")
        return 2
    failed = 0
    for name, paper, expected in cases:
        started = time.time()
        try:
            actual = await read_health_document(str(paper), None)
        except Exception as exc:                      # a case that cannot be read is a failure, not a crash
            print(f"FAIL {name}: could not be read: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        result = benchmark.compare(name, expected, actual)
        status = "PASS" if result.passed else "FAIL"
        review = "" if expected.get("reviewed") else "  (expected reading not yet reviewed by a person)"
        print(f"{status} {name}: {result.found}/{result.expected} results found, {round(time.time() - started)} s{review}")
        for line in result.problems:
            print(f"    - {line}")
        for line in result.notes:
            print(f"    · {line}")
        failed += 0 if result.passed else 1
    print(f"{len(cases) - failed}/{len(cases)} cases passed")
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--case")
    promote = sub.add_parser("promote")
    promote.add_argument("document_id", type=int)
    promote.add_argument("name")
    args = parser.parse_args()
    if args.command == "promote":
        with Session(engine) as session:
            folder = benchmark.promote(session, args.document_id, args.name)
        print(f"Written to {folder}. Check expected.json against the paper, then set \"reviewed\": true.")
        return 0
    return asyncio.run(_run(args.case))


if __name__ == "__main__":
    sys.exit(main())
