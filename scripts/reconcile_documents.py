"""Reconcile bills, invoices and receipts to the bank rows that paid them. Dry run unless --apply.
It also runs by itself whenever a bill, a statement or a bank sync arrives; this is for the backlog.
Back up the DB first (docs/SYSADMIN.md).

    python scripts/reconcile_documents.py                     # dry run, lists the pairs and what did not match
    python scripts/reconcile_documents.py --apply [--log FILE]  # FILE: the (document row, bank row) pairs as JSON
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine  # noqa: E402
from app.services.document_reconcile import reconcile_documents  # noqa: E402


def main() -> None:
    apply = "--apply" in sys.argv
    log = sys.argv[sys.argv.index("--log") + 1] if "--log" in sys.argv else None
    with Session(engine) as session:
        report = reconcile_documents(session, dry_run=not apply)
    print(f"{'settled' if apply else 'would settle'}: {report.settled}", flush=True)
    for doc_id, bank_id in report.pairs:
        print(f"  document row {doc_id} -> bank row {bank_id}")
    print(f"not matched ({len(report.unmatched)}):")
    for u in report.unmatched:
        print(f"  row {u.transaction_id} {u.provider!r} {u.amount:.2f} on {u.when}; closest same-payee debit: {u.nearest}")
    if log:
        Path(log).write_text(json.dumps(report.pairs))


if __name__ == "__main__":
    main()
