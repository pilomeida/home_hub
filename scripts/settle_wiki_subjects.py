"""One-off (PLAN step 6a, 6b, 2026-11-20): give the wiki pages already on file their subject, add the providers'
other spellings as aliases (Amigo → Vodafone Portugal), join the topic pages about one provider, fold the fact keys
to one spelling, and write each provider fact as its provider's name. New pages get all this as they are filed.

Run on the VPS, from the app folder, as the app's user, after the migration (back up the database first):
    python scripts/settle_wiki_subjects.py            # dry run: what it would do
    python scripts/settle_wiki_subjects.py --apply
Safe to run again."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine  # noqa: E402
from app.models.subject import Subject  # noqa: E402
from app.models.wiki import ClaimStatus, WikiClaim, WikiPage  # noqa: E402
from app.services import wiki_subjects  # noqa: E402


def _counts(session: Session) -> str:
    pages = len(session.exec(select(WikiPage)).all())
    active = len(session.exec(select(WikiClaim).where(WikiClaim.status == ClaimStatus.ACTIVE)).all())
    every = len(session.exec(select(WikiClaim)).all())
    return f"{pages} pages, {active} current facts, {every} facts in all"


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    with Session(engine) as session:
        print("before:", _counts(session))
        out = wiki_subjects.settle(session)
        for key, value in out.items():
            print(f"{key}: {value}")
        print("after: ", _counts(session))
        for page in session.exec(select(WikiPage).order_by(WikiPage.id)).all():
            about = session.get(Subject, wiki_subjects.root_id(session, page.subject_id)) if page.subject_id else None
            print(f"  page {page.id} {page.topic!r}: {f'{about.kind.value} {about.name!r}' if about else 'no subject'}")
        if apply:
            session.commit()
            print("applied")
        else:
            session.rollback()
            print("dry run: nothing changed (add --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
