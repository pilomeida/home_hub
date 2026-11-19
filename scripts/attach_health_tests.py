"""One-off (2026-11-19, PLAN step 5b): give the lab results already on file their known test, and the lab sheets
their topics (written reports had topics already). New papers get both when they are read.

Run on the VPS, from the app folder, as the app's user, after the migration:
    python scripts/attach_health_tests.py            # dry run: how many results and sheets, nothing written
    python scripts/attach_health_tests.py --apply    # write
Only `health_tests`, `health_test_aliases`, `health_topics` and the `test_id`/`topic_id` of Health results are
written; no value, date or paper changes. No AI. Safe to run again (results that have a test keep it)."""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.categories.health import tests, topics  # noqa: E402
from app.db import engine  # noqa: E402
from app.models.health import HealthEntry, HealthMeasurement, HealthTest, HealthTopic  # noqa: E402


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    with Session(engine) as session:
        entries = session.exec(select(HealthEntry).order_by(HealthEntry.id)).all()
        layouts = Counter(e.layout or "not said" for e in entries)
        todo = [e for e in entries if e.layout != "report"]
        waiting = session.exec(select(HealthMeasurement).where(
            HealthMeasurement.entry_id.in_([e.id for e in todo]), HealthMeasurement.test_id.is_(None))).all() if todo else []
        print(f"{len(entries)} Health papers ({dict(layouts)}); {len(waiting)} results on {len(todo)} non-report papers "
              f"have no test yet")
        if not apply:
            print("dry run: nothing changed (add --apply)")
            return 0
        topics_before = len(session.exec(select(HealthTopic)).all())
        matched = 0
        for entry in todo:
            topics.attach_topics(session, entry)
            matched += tests.attach_tests(session, entry)
        all_tests = session.exec(select(HealthTest)).all()
        print(f"matched {matched} results to {len(all_tests)} tests ({sum(1 for t in all_tests if not t.approved)} "
              f"waiting for a person); topics {topics_before} -> {len(session.exec(select(HealthTopic)).all())}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
