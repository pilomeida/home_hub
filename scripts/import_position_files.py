"""Feed Santander statement PDFs and loan-history printouts through the same
pipeline as the Loans & Savings upload page (app/routers/loans.py), without the
web UI. Run on the server as the app user, from /srv/home-hub/app:

    python scripts/import_position_files.py --statements s1.pdf s2.pdf --histories h1.pdf
    python scripts/import_position_files.py --histories h1.pdf --new-loan 123456789012:Car loan:personal
    python scripts/import_position_files.py --dry-run --statements s1.pdf

All statements are read first (in the order given), then all histories. One line
per file: OK|ALREADY|NEEDS_LOAN|FAILED|REJECTED  <file>  <message>. Idempotent:
re-running the same files reports ALREADY. Input files are never moved or deleted.
Exit: 0 ok, 1 a file FAILED, 2 usage error or ambiguous assignment.
"""

import argparse
import asyncio
import io
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import select  # noqa: E402
from starlette.datastructures import UploadFile  # noqa: E402

from app.models.debt import Debt  # noqa: E402
from app.models.document import Document  # noqa: E402
from app.models.position import PositionExtraction  # noqa: E402
from app.routers.loans import HISTORIES, LOAN_TYPES, STATEMENTS, _SLOT, _from_extraction, _handle_file  # noqa: E402
from app.services import position_extraction  # noqa: E402
from app.services.pdf_bytes import NOT_PDF, inspect_pdf  # noqa: E402
from app.services.position_store import (  # noqa: E402
    assign_history_to_loan, create_loan_from_assignment, loan_number_conflict,
)

_LABEL = {"read": "OK", "already_read": "ALREADY", "needs_loan": "NEEDS_LOAN", "failed": "FAILED",
          "rejected": "REJECTED", "other": "REJECTED"}
_NEW_LOAN = re.compile(r"(\d{8,}):([^:]+):(" + "|".join(LOAN_TYPES) + ")")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Import Santander statements / loan histories into Loans & Savings.")
    p.add_argument("--statements", nargs="+", default=[], metavar="FILE", help="monthly statement PDFs")
    p.add_argument("--histories", nargs="+", default=[], metavar="FILE", help="loan-history printout PDFs")
    p.add_argument("--new-loan", metavar="DIGITS:NAME:TYPE",
                   help="assign the one printout that matches no loan to a new loan (TYPE: mortgage|personal|other)")
    p.add_argument("--dry-run", action="store_true", help="list the files and check they are PDFs; change nothing")
    p.add_argument("--user", default="script", help="label recorded as the uploader (default: script)")
    return p


def _line(label: str, name: str, message: str) -> None:
    print(f"{label}  {name}  {message}".rstrip())


@contextmanager
def _gateway_override(gateway):
    """Tests inject a fake: the ingestion path reads the gateway from
    position_extraction.get_gateway, so that is the one place to swap."""
    if gateway is None:
        yield
        return
    original = position_extraction.get_gateway
    position_extraction.get_gateway = lambda: gateway
    try:
        yield
    finally:
        position_extraction.get_gateway = original


def _dry_run(plan: list[tuple[str, Path]]) -> int:
    for slot, path in plan:
        data = path.read_bytes()
        clean, stripped, problem = inspect_pdf(data)
        if clean is None or not path.name.lower().endswith(".pdf"):
            _line("REJECTED", path.name, f"{slot}, {len(data)} bytes: {problem or NOT_PDF}")
        elif stripped:
            _line("OK (wrapped, %d bytes stripped)" % stripped, path.name, f"{slot}, {len(data)} bytes")
        else:
            _line("OK", path.name, f"{slot}, {len(data)} bytes")
    return 0


async def _process(session, plan, user, gateway) -> list:
    request = SimpleNamespace(state=SimpleNamespace(user_email=user))
    results = []
    for slot, path in plan:
        upload = UploadFile(file=io.BytesIO(path.read_bytes()), filename=path.name)
        with _gateway_override(gateway):
            results.append(await _handle_file(session, request, upload, slot))
    return results


def _assign(session, pending, new_loan) -> int:
    number, name, loan_type = new_loan
    if not pending:
        print("nothing to assign")
        return 0
    if len(pending) > 1:
        print(f"{len(pending)} printouts need a loan; not assigning (assign each through the page):")
        for r in pending:
            print(f"  {r.name}  extraction {r.extraction_id}")
        return 2
    result = pending[0]
    extraction = session.get(PositionExtraction, result.extraction_id)
    debt = session.exec(select(Debt).where(Debt.external_number == number)).first()
    if debt is None:
        problem = loan_number_conflict(session, number)
        if problem:
            _line("FAILED", result.name, problem)
            return 1
        debt = create_loan_from_assignment(session, number, name.strip(), loan_type)
    try:
        assign_history_to_loan(session, extraction, debt)  # also back-links transactions
    except ValueError as exc:
        session.rollback()
        _line("FAILED", result.name, str(exc))
        return 1
    session.refresh(extraction)
    document = session.get(Document, extraction.document_id)
    done = _from_extraction(session, result.name, _SLOT[HISTORIES]["label"], extraction, document, already=False)
    _line("OK", result.name, f"assigned to the new loan: {done.message}")
    return 0


def main(argv, session_factory=None, gateway=None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    if not args.statements and not args.histories:
        print("error: give at least one file with --statements or --histories", file=sys.stderr)
        return 2
    new_loan = None
    if args.new_loan is not None:
        m = _NEW_LOAN.fullmatch(args.new_loan)
        if not m or not m.group(2).strip():
            print("error: --new-loan must look like DIGITS:NAME:TYPE (8+ digits, NAME without ':', "
                  f"TYPE one of {', '.join(LOAN_TYPES)})", file=sys.stderr)
            return 2
        new_loan = m.groups()
    plan = [(STATEMENTS, Path(f)) for f in args.statements] + [(HISTORIES, Path(f)) for f in args.histories]
    missing = [p for _, p in plan if not p.is_file()]
    if missing:
        for p in missing:
            print(f"error: no such file: {p}", file=sys.stderr)
        return 2
    if args.dry_run:
        return _dry_run(plan)

    if session_factory is None:
        from app.db import get_session_factory
        session_factory = get_session_factory()
    with session_factory() as session:
        results = asyncio.run(_process(session, plan, args.user, gateway))
        for r in results:
            _line(_LABEL.get(r.outcome, "FAILED"), r.name, r.message)
        pending = [r for r in results if r.outcome == "needs_loan" and r.extraction_id is not None]
        code = 1 if any(r.outcome == "failed" for r in results) else 0
        if new_loan is not None:
            assigned = _assign(session, pending, new_loan)
            return max(code, assigned)
        for r in pending:
            print(f"  {r.name}: extraction {r.extraction_id} needs a loan - assign it on the Loans upload page "
                  "or re-run with --new-loan DIGITS:NAME:TYPE")
        return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
