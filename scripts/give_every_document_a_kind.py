"""One-off (work-list B6, 2026-11-03): every filed document gets a paper kind.

A document waiting in the Inbox has no kind on purpose (a person chooses it there), and a discarded one needs none.
Every other document must have one. On 2026-11-03 five Money documents had none, all from the first uploads of
2026-08-18, before the kind was decided for every paper:
- four bank statements whose reading failed then (EXTCON… from Santander, account-statement… from Revolut): kind
  "statement". The Revolut history was also attached to the Santander account; it moves to the Revolut account.
- the MetLife premium paper (one line, already filed): kind "bill".
The rule is by file name and line count, printed for every document; nothing is read again and no line changes.
A paper on a kind that no longer exists (Health's medical_bill) is only reported: scripts/file_dental_receipt.py
moves it.

    python scripts/give_every_document_a_kind.py            # dry run: prints what it would do
    python scripts/give_every_document_a_kind.py --apply    # does it (on the VPS, from the app folder, as the app's user)
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, func, select  # noqa: E402

from app.db import engine  # noqa: E402
from app.domains.base import UnknownKindError  # noqa: E402
from app.domains.registry import get_spec  # noqa: E402
from app.models.account import Account  # noqa: E402
from app.models.document import Document, DocumentStatus  # noqa: E402
from app.models.domain import Domain  # noqa: E402
from app.models.transaction import Transaction  # noqa: E402
from app.privacy.settings import configured_tier  # noqa: E402

WAITING = (DocumentStatus.PENDING_REVIEW, DocumentStatus.DISCARDED)
SANTANDER_STATEMENT = re.compile(r"^EXTCON\d+\.pdf$", re.I)
REVOLUT_STATEMENT = re.compile(r"^account-statement_.*\.pdf$", re.I)


def decide(session: Session, document: Document) -> tuple[str, int | None] | None:
    """(kind, account to move it to or None), or None when no rule fits and a person must choose."""
    if document.domain != Domain.FINANCIALS:
        return None
    if SANTANDER_STATEMENT.match(document.filename):
        return "statement", None
    if REVOLUT_STATEMENT.match(document.filename):
        revolut = session.exec(select(Account).where(Account.institution.like("Revolut%"))).first()
        moved = revolut.id if revolut is not None and document.account_id != revolut.id else None
        return "statement", moved
    lines = session.exec(select(func.count()).select_from(Transaction).where(Transaction.document_id == document.id)).one()
    if lines == 1:
        return "bill", None
    return None


def run(session: Session, apply: bool) -> list[str]:
    report = []
    kindless = session.exec(select(Document).where(Document.paper_kind.is_(None), Document.status.not_in(WAITING))).all()
    for document in kindless:
        decision = decide(session, document)
        if decision is None:
            report.append(f"document {document.id} {document.filename}: no rule fits; choose its kind on its page")
            continue
        kind, account_id = decision
        note = f" and move it to account {account_id}" if account_id is not None else ""
        report.append(f"document {document.id} {document.filename} ({document.status.value}): kind {kind}{note}")
        if apply:
            document.paper_kind = kind
            document.sensitivity_tier = configured_tier(session, document.domain, kind)
            if account_id is not None:
                document.account_id = account_id
            session.add(document)
    for document in session.exec(select(Document).where(Document.paper_kind.is_not(None), Document.domain.is_not(None))).all():
        try:
            get_spec(document.domain).kind(document.paper_kind)
        except UnknownKindError:
            report.append(f"document {document.id} {document.filename}: kind {document.paper_kind!r} no longer exists "
                          "in its category; move it (for the dental receipt: scripts/file_dental_receipt.py)")
    if apply:
        session.commit()
    return report


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    with Session(engine) as session:
        for line in run(session, apply):
            print(line)
    print("applied" if apply else "dry run: nothing changed (add --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
