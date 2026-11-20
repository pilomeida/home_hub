"""PLAN step 9 D11: read part of a statement too big to read whole, in parts of a few pages, and file only the lines of
a date range. Made for Revolut document 51 (2022-08 -> 2026-08, 221 pages, refused whole by the AI provider): pages
1-34 are Pedro's own account up to 2023-12; later months are on the monthly statements and the bank feed, and the
pages after 95 are other people's Revolut products, so they are never read here.

Run on the VPS, from the app folder, as the app's user (back up the database first):
    python scripts/read_statement_part.py --document 51 --pages 1-34 --from 2022-08-01 --before 2024-01-01
        dry run: reads the pages (AI calls, a few cents), saves the reading under data/readings/, prints what it
        would file, the running-balance check and the shown totals; changes nothing
    python scripts/read_statement_part.py ... --apply
        files the saved reading (no second reading), classifies the lines the usual way, and prints the shown
        totals before and after (lines before the books' start, 2025-10-10, are kept but never shown or counted)
Refuses to file when the account already has lines in the range (nothing is filed twice)."""

import argparse
import asyncio
import json
import sys
from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app.categories.financials.handler import line_type  # noqa: E402
from app.db import engine  # noqa: E402
from app.models.document import Document, DocumentStatus  # noqa: E402
from app.models.transaction import Transaction, TransactionType  # noqa: E402
from app.privacy.scope import privacy_scope  # noqa: E402
from app.services.classification_engine import classify_transaction  # noqa: E402
from app.services.extraction import ExtractedStatement, ExtractedTransaction, extract_statement_in_parts  # noqa: E402
from app.services.statement_check import apply_statement_check  # noqa: E402

READINGS = Path("data/readings")


def _shown_totals(session: Session) -> tuple[int, float]:
    """How many lines are counted anywhere, and their net, as every total sees them."""
    rows = session.exec(select(Transaction.transaction_type, func.count(), func.sum(Transaction.amount))
                        .where(Transaction.counts()).group_by(Transaction.transaction_type)).all()
    count = sum(n for _, n, _ in rows)
    net = sum((total or 0.0) * (1 if kind == TransactionType.CREDIT else -1) for kind, _, total in rows)
    return count, round(net, 2)


def _settle_directions(lines: list[ExtractedTransaction]) -> int:
    """Where the printed running balance before and after a line proves which way the money went, that wins over the
    reader's label (document 51: the reader called many "Transfer from ..." and top-ups money out while the balance
    rose by exactly their amount). Returns how many labels changed; a line the balances do not prove keeps its own."""
    changed = 0
    for previous, line in zip(lines, lines[1:]):
        if previous.balance_after is None or line.balance_after is None:
            continue
        delta = round(line.balance_after - previous.balance_after, 2)
        if delta == 0 or abs(abs(delta) - abs(line.amount)) > 0.01:
            continue
        proven = TransactionType.CREDIT if delta > 0 else TransactionType.DEBIT
        if line_type(line.transaction_type) != proven:
            line.transaction_type = proven.value
            changed += 1
    return changed


def _balance_breaks(lines: list[ExtractedTransaction]) -> list[str]:
    """Lines whose printed running balance does not follow from the line before (a misread amount or a lost line)."""
    breaks, previous = [], None
    for line in lines:
        if line.balance_after is None:
            previous = None
            continue
        if previous is not None:
            sign = 1 if line_type(line.transaction_type) == TransactionType.CREDIT else -1
            if abs(previous + sign * abs(line.amount) - line.balance_after) > 0.01:
                breaks.append(f"{line.transaction_date} {line.description[:30]!r} {line.amount:.2f} -> "
                              f"{line.balance_after:.2f} (after {previous:.2f})")
        previous = line.balance_after
    return breaks


async def _reading(doc: Document, first: int, last: int, per_part: int, session: Session) -> list[ExtractedTransaction]:
    saved = READINGS / f"document-{doc.id}-pages-{first}-{last}.json"
    if saved.exists():
        print(f"using the saved reading {saved}")
        rows = json.loads(saved.read_text())
        return [ExtractedTransaction(**{**r, "transaction_date": date.fromisoformat(r["transaction_date"])}) for r in rows]
    with privacy_scope(session, doc.category, doc.paper_kind):          # the paper's own level
        read = await extract_statement_in_parts(doc.file_path, first, last, pages_per_part=per_part)
    READINGS.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps([{**asdict(t), "transaction_date": t.transaction_date.isoformat()}
                                 for t in read.transactions], ensure_ascii=False))
    print(f"read pages {first}-{last} in parts of {per_part}: {len(read.transactions)} lines, saved to {saved}")
    return read.transactions


async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--document", type=int, required=True)
    ap.add_argument("--pages", required=True, help="first-last, 1-based")
    ap.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    ap.add_argument("--before", type=date.fromisoformat, required=True)
    ap.add_argument("--per-part", type=int, default=4)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    first, last = (int(p) for p in args.pages.split("-"))

    with Session(engine) as session:
        doc = session.get(Document, args.document)
        if doc is None or doc.account_id is None:
            print(f"Not read. Document {args.document} is not a statement of a known account.")
            return 1
        lines = await _reading(doc, first, last, args.per_part, session)
        print(f"direction settled by the running balance on {_settle_directions(lines)} line(s)")
        kept = [t for t in lines if args.start <= t.transaction_date < args.before]
        print(f"{len(kept)} lines from {args.start} to before {args.before} ({len(lines) - len(kept)} outside, left out)")
        print("per month:", dict(sorted(Counter(t.transaction_date.strftime("%Y-%m") for t in kept).items())))
        money_in = sum(abs(t.amount) for t in kept if line_type(t.transaction_type) == TransactionType.CREDIT)
        print(f"in {money_in:.2f}, out {sum(abs(t.amount) for t in kept) - money_in:.2f}")
        breaks = _balance_breaks(kept)
        print(f"running balance: {len(breaks)} break(s)" + "".join(f"\n  {b}" for b in breaks[:10]))
        already = session.exec(select(func.count()).select_from(Transaction).where(
            Transaction.account_id == doc.account_id, Transaction.paid_date >= args.start,
            Transaction.paid_date < args.before)).one()
        before = _shown_totals(session)
        print(f"lines already on the account in the range: {already}; shown totals now: {before[0]} lines, net {before[1]:.2f}")
        if not args.apply:
            print("dry run: nothing changed (add --apply)")
            return 0
        if already:
            print("Not filed. The account already has lines in this range, so filing would double them.")
            return 1

        filed = []
        for t in kept:
            txn = Transaction(document_id=doc.id, account_id=doc.account_id, description=t.description,
                              transaction_type=line_type(t.transaction_type), amount=abs(t.amount),
                              currency=t.currency, paid_date=t.transaction_date,
                              statement_period=t.transaction_date.strftime("%Y-%m"))
            session.add(txn)
            filed.append(txn)
        # The statement check, as for any statement: a line still in doubt waits for a person (decision D17).
        first_line = kept[0] if kept else None
        opening = None
        if first_line is not None and first_line.balance_after is not None:
            sign = 1 if line_type(first_line.transaction_type) == TransactionType.CREDIT else -1
            opening = round(first_line.balance_after - sign * abs(first_line.amount), 2)
        closing = kept[-1].balance_after if kept else None
        session.flush()
        verdict = apply_statement_check(session, doc.id, filed, ExtractedStatement(
            statement_period=None, transactions=kept, opening_balance=opening, closing_balance=closing))
        session.commit()
        print(f"statement check: {verdict.status}, {len(verdict.flagged)} line(s) wait for a person")
        unclassified = 0
        with privacy_scope(session, doc.category, doc.paper_kind):
            answers = await classify_transaction.prefetch(session, [(t.description, t.transaction_type == TransactionType.CREDIT)
                                                                   for t in filed])
            for txn in filed:
                try:
                    await classify_transaction(session, txn, answers=answers)
                    session.commit()
                except Exception as exc:  # noqa: BLE001: the line stays, unfiled, for a person (Needs Review)
                    session.rollback()
                    unclassified += 1
                    print(f"  line {txn.id} not classified: {exc}")
        doc.status, doc.failure_reason = DocumentStatus.PROCESSED, None
        session.add(doc)
        from app.models.wiki import WikiOperation
        from app.services.wiki_store import append_log
        append_log(session, WikiOperation.INGEST, f"{doc.filename}: pages {first}-{last} read in parts, {len(filed)} "
                   f"line(s) from {args.start} to before {args.before} filed (PLAN step 9 D11); later months are on the "
                   f"monthly statements and the bank feed", document_id=doc.id)
        session.commit()
        after = _shown_totals(session)
        print(f"filed {len(filed)} lines ({unclassified} left for a person); shown totals after: {after[0]} lines, "
              f"net {after[1]:.2f} ({'unchanged' if after == before else 'CHANGED'})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
