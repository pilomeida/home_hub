"""Reconcile documents to the bank: the bank statements are the source of truth.

A bill, invoice or receipt makes a Transaction row that is only the DOCUMENT's record of a payment (its
date is the bill's, not the day the money left). When the bank statements (or the bank sync) show that
payment, the document row is "settled by" the bank row: the bank row is the transaction, the document
becomes its attachment, and the settled row counts in no total (every money aggregate filters
`settled_by_id IS NULL`). Matching is conservative: same amount, a payee word in common, a bounded date
window, one bank row per document. What does not match stays as it is and is reported for a human."""

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models.document import Document, DocumentSource
from app.models.merchant import Merchant
from app.models.todo import Todo
from app.models.transaction import Transaction, TransactionType

DAYS_BANK_BEFORE_DOCUMENT = 5     # a receipt is often issued a few days after the debit
DAYS_BANK_AFTER_DOCUMENT = 60     # a bill precedes its direct debit by 3 to 6 weeks
_STOP = {"debito", "direto", "debit", "direct", "pagamento", "compra", "transferencia", "transfer", "payment",
         "europe", "europa", "seguros", "insurance", "portugal", "lda", "companhia", "fatura", "recibo", "servicos"}
_UNKNOWN_BANK_DOCS = ("statement",)


@dataclass
class Unmatched:
    transaction_id: int
    provider: str
    amount: float
    when: Optional[date]
    nearest: Optional[tuple[int, float, date]]  # (bank id, amount, date) of the closest same-payee debit, any amount


@dataclass
class ReconcileDocsReport:
    settled: int = 0
    pairs: list[tuple[int, int]] = field(default_factory=list)  # (document row, bank row)
    unmatched: list[Unmatched] = field(default_factory=list)


def _tokens(*texts: Optional[str]) -> set[str]:
    out = set()
    for text in texts:
        s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
        out |= {w[:6] for w in re.split(r"[^a-z0-9]+", s) if len(w) >= 4 and not w.isdigit() and w not in _STOP}
    return out


def _is_bank(document: Document) -> bool:
    return document.category in _UNKNOWN_BANK_DOCS or document.source == DocumentSource.API


def reconcile_quietly(session: Session) -> None:
    """The automatic call after a bill, a statement or a bank sync lands. Reconciliation is enrichment: a
    failure must never undo the ingestion that triggered it."""
    try:
        reconcile_documents(session)
    except Exception:  # noqa: BLE001
        session.rollback()
        logging.getLogger(__name__).exception("document reconciliation failed")


def reconcile_documents(session: Session, dry_run: bool = False) -> ReconcileDocsReport:
    report = ReconcileDocsReport()
    rows = session.exec(select(Transaction, Document).join(Document, Document.id == Transaction.document_id)
                        .where(Transaction.transaction_type == TransactionType.DEBIT)).all()
    names = {m.id: m.canonical_name for m in session.exec(select(Merchant)).all()}
    candidates = [t for t, d in rows if not _is_bank(d) and t.settled_by_id is None and (t.paid_date or t.due_date)]
    banks = [t for t, d in rows if _is_bank(d)]
    used = {t.settled_by_id for t, _d in rows if t.settled_by_id is not None}
    bank_tokens = {b.id: _tokens(b.provider, names.get(b.merchant_id)) for b in banks}

    by_id = {t.id: t for t, _d in rows}
    done: set[int] = set()
    # Oldest document first, each taking its nearest unused bank row: a June bill never grabs the debit of
    # the May one, and a receipt issued days after its debit still finds that debit.
    for c in sorted(candidates, key=lambda t: ((t.paid_date or t.due_date), t.id)):
        ref = c.paid_date or c.due_date
        ctokens = _tokens(c.provider, names.get(c.merchant_id))
        lo, hi = ref - timedelta(days=DAYS_BANK_BEFORE_DOCUMENT), ref + timedelta(days=DAYS_BANK_AFTER_DOCUMENT)
        options = [b for b in banks if b.id not in used and b.paid_date and lo <= b.paid_date <= hi
                   and abs(b.amount - c.amount) < 0.005 and ctokens & bank_tokens[b.id]]
        if not options:
            continue
        bank_row = min(options, key=lambda b: (abs((b.paid_date - ref).days), b.id))
        done.add(c.id); used.add(bank_row.id)
        report.pairs.append((c.id, bank_row.id))
        report.settled += 1
        if dry_run:
            continue
        c.settled_by_id = bank_row.id
        if c.commitment_id and bank_row.commitment_id is None:
            bank_row.commitment_id = c.commitment_id  # the payment keeps counting toward its commitment
            session.add(bank_row)
        for todo in session.exec(select(Todo).where(Todo.transaction_id == c.id, Todo.done.is_(False))).all():
            todo.done = True
            session.add(todo)
        session.add(c)

    for c in candidates:
        if c.id in done:
            continue
        ref = c.paid_date or c.due_date
        ctokens = _tokens(c.provider, names.get(c.merchant_id))
        near = [b for b in banks if b.paid_date and abs((b.paid_date - ref).days) <= DAYS_BANK_AFTER_DOCUMENT
                and ctokens & bank_tokens[b.id]]
        best = min(near, key=lambda b: abs((b.paid_date - ref).days), default=None)
        report.unmatched.append(Unmatched(c.id, c.provider, c.amount, ref,
                                          (best.id, best.amount, best.paid_date) if best else None))
    if not dry_run:
        session.commit()
    return report
