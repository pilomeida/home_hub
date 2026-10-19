"""Register Rute's card statements (account 4): keep each PDF as a document, store its lines on the card, and
turn her "not itemised" payments back into transfers (the card's own lines are now the spending). Dry run
unless --apply. Safe to re-run: documents dedupe by content, lines already stored are skipped.

    python scripts/add_rute_card_statements.py statement1.pdf statement2.pdf ... [--apply] [--app-line DD-MM-YYYY:AMOUNT:TEXT]

--app-line adds a line seen in the Santander app after the last statement (it is not on any statement yet).
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine  # noqa: E402
from app.models.document import Document, DocumentSource, DocumentStatus  # noqa: E402
from app.models.domain import Domain  # noqa: E402
from app.services.card_figures import (  # noqa: E402
    CardLine, add_lines, presort_purchases, read_card_statement, reconcile, unfile_rute_card_payments,
)
from app.services.ingestion import IncomingFile, receive_file  # noqa: E402
from app.services.taxonomy import ensure_taxonomy  # noqa: E402

CARD_ACCOUNT_ID = 4


def _app_lines(args: list[str]) -> list[CardLine]:
    out = []
    for i, a in enumerate(args):
        if a == "--app-line":
            d, amount, text = args[i + 1].split(":", 2)
            day, month, year = d.split("-")
            out.append(CardLine(date(int(year), int(month), int(day)), text, float(amount)))
    return out


def main() -> None:
    args = sys.argv[1:]
    apply = "--apply" in args
    skip = {i + 1 for i, a in enumerate(args) if a == "--app-line"}
    paths = [a for i, a in enumerate(args) if not a.startswith("--") and i not in skip]
    statements = sorted(((read_card_statement(p), p) for p in paths), key=lambda sp: sp[0].number)
    with Session(engine) as session:
        if apply:
            ensure_taxonomy(session)
            session.commit()
        for st, path in statements:
            todo, redates = reconcile(session, CARD_ACCOUNT_ID, st.lines)
            print(f"Statement {st.number} ({st.issued}): {len(st.lines)} lines, {len(todo)} to add "
                  f"(opening {st.opening:.2f}, closing {st.closing:.2f}, checked to the cent)")
            for l in todo:
                print(f"  {l.day}  {l.amount:>9.2f}  {l.description}")
            if apply and todo:
                result = receive_file(session, IncomingFile(Path(path).name, Path(path).read_bytes(),
                                                            DocumentSource.MANUAL, "claude-rollout"))
                doc = result.document
                doc.status, doc.category, doc.domain, doc.account_id = (
                    DocumentStatus.PROCESSED, "statement", Domain.FINANCIALS, CARD_ACCOUNT_ID)
                doc.fields_json = json.dumps({"statement_number": st.number, "issued": str(st.issued),
                                              "opening_balance": st.opening, "closing_balance": st.closing})
                session.add(doc)
                session.commit()
                print(f"  stored {add_lines(session, CARD_ACCOUNT_ID, todo, document_id=doc.id)} on document {doc.id}")
        extra = _app_lines(args)
        if extra:
            todo, _ = reconcile(session, CARD_ACCOUNT_ID, extra)
            for l in todo:
                print(f"App line: {l.day}  {l.amount:>9.2f}  {l.description}")
            if apply and todo:
                doc = session.exec(select(Document).where(Document.account_id == CARD_ACCOUNT_ID,
                                                          Document.source == DocumentSource.API)).first()
                print(f"  stored {add_lines(session, CARD_ACCOUNT_ID, todo, document_id=doc.id if doc else None)}")
        rute = rute_card_payments_filed(session)
        print(f"{len(rute)} payment(s) now counted as 'not itemised' go back to transfers ({sum(t.amount for t in rute):.2f}):")
        for t in rute:
            print(f"  {t.paid_date}  {t.amount:>9.2f}  {t.description}")
        if apply:
            print(f"Switched {len(unfile_rute_card_payments(session))}.")
            print(f"Filed {len(presort_purchases(session, CARD_ACCOUNT_ID))} waiting purchase(s) by merchant memory.")
        else:
            print("Dry run. Add --apply to write.")


def rute_card_payments_filed(session: Session):
    from app.models.transaction import Transaction
    from app.services.card_figures import RUTE_CARD_MARK, RUTE_CARD_NODE
    from app.services.taxonomy import get_node
    node = get_node(session, RUTE_CARD_NODE)
    return [] if node is None else session.exec(select(Transaction).where(
        Transaction.description.contains(RUTE_CARD_MARK), Transaction.category_id == node.id)).all()


if __name__ == "__main__":
    main()
