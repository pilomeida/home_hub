"""One-off (2026-11-17): give the wiki facts already on file the dates they were true, from their papers
(Graphiti's valid time, migration a3c7e1f9b250). New facts are dated as they are filed.

Run on the VPS, from the app folder, as the app's user, after the migration:
    python scripts/date_wiki_facts.py            # dry run: what it would date
    python scripts/date_wiki_facts.py --apply
Safe to run again: only empty dates are filled. A fact replaced by one from an OLDER paper is listed, not changed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.models.wiki import WikiClaim, WikiPage  # noqa: E402
from app.services.wiki_store import date_existing_facts  # noqa: E402


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    with Session(engine) as session:
        report = date_existing_facts(session)
        print(f"dated {report.dated} facts from their papers; closed {report.closed} replaced facts")
        for claim_id in report.out_of_order:
            claim = session.get(WikiClaim, claim_id)
            page = session.get(WikiPage, claim.page_id)
            print(f"  replaced by an older paper: {page.topic} / {claim.key} = {claim.value} (claim {claim.id})")
        if apply:
            session.commit()
        else:
            session.rollback()
            print("dry run: nothing changed (add --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
