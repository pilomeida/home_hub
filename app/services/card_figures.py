"""Credit-card figures (Pedro, 2026-10-18).

Pedro's card (…1835) is itemised by the bank's card list: the lines the bank sync has not stored yet are added
here from that list. Rute's card (…7303599 on the statements) has no list the app can read; its only trace is
the monthly payment from the current account, so those payments count as spending on "Rute card (not
itemised)" instead of hiding as a transfer. Pedro's own card payments stay transfers: their purchases are
itemised."""
import hashlib
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlmodel import Session, select

from app.baseline import BASELINE_DATE
from app.models.merchant import Merchant
from app.models.transaction import Category, Nature, Transaction, TransactionType
from app.services.taxonomy import file_transaction, get_node

RUTE_CARD_NODE = "family.personal.rute-card-not-itemised"
RUTE_CARD_MARK = "7303599"          # the payment reference of Rute's card on the current-account statement
_LINE = re.compile(r"^\s*(\d\d)-(\d\d)-(\d{4})\s+(.+?)\s+([+-]\d[\d.]*,\d\d) EUR\s*$")


@dataclass(frozen=True)
class CardLine:
    day: date
    description: str
    amount: float          # positive money in (payments, refunds), negative money out
    @property
    def credit(self) -> bool:
        return self.amount > 0


def parse_card_list(text: str) -> list[CardLine]:
    """The lines of the bank's "Consulta Movimentos Cartão Créd" page, from its text."""
    out = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            d, mo, y, desc, amount = m.groups()
            out.append(CardLine(date(int(y), int(mo), int(d)), desc.replace("*", " ").replace("  ", " ").strip(),
                                float(amount.replace(".", "").replace(",", "."))))
    return out


def read_card_pdf(path: str) -> list[CardLine]:
    return parse_card_list(subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True,
                                          check=True).stdout)


def reconcile(session: Session, account_id: int, lines: list[CardLine]) -> tuple[list[CardLine], list[tuple[Transaction, CardLine]]]:
    """(lines to add, stored rows to re-date) for lines on or after the baseline. A line is held when a row has
    the same day, amount and direction (matched one-to-one). The card feed stamps some rows with the day they were
    fetched, so a leftover row with the same amount and direction but a LATER day is that line, mis-dated."""
    rows = session.exec(select(Transaction).where(Transaction.account_id == account_id)).all()
    free: dict[tuple, list[Transaction]] = {}
    for t in rows:
        free.setdefault((round(t.amount, 2), t.transaction_type == TransactionType.CREDIT), []).append(t)
    todo = []
    for line in lines:
        if line.day < BASELINE_DATE:
            continue
        pool = free.get((round(abs(line.amount), 2), line.credit), [])
        exact = next((t for t in pool if t.paid_date == line.day), None)
        if exact is not None:
            pool.remove(exact)
        else:
            todo.append(line)
    redates = []
    for line in list(todo):
        pool = free.get((round(abs(line.amount), 2), line.credit), [])
        later = next((t for t in pool if t.paid_date and t.paid_date > line.day
                      and not any(l.day == t.paid_date and round(abs(l.amount), 2) == round(t.amount, 2)
                                  and l.credit == line.credit for l in lines)), None)
        if later is not None:
            pool.remove(later)
            redates.append((later, line))
            todo.remove(line)
    return todo, redates


def missing_lines(session: Session, account_id: int, lines: list[CardLine]) -> list[CardLine]:
    return reconcile(session, account_id, lines)[0]


def redate_rows(session: Session, pairs: list[tuple[Transaction, CardLine]]) -> None:
    for t, line in pairs:
        t.paid_date = line.day
        t.statement_period = line.day.strftime("%Y-%m")
        session.add(t)
    session.commit()


def _stem(description: str) -> str:
    return re.sub(r"[-\s]*\d{4}$", "", description.upper().replace("CRÉDITO", "CREDITO")).strip()


def add_lines(session: Session, account_id: int, lines: list[CardLine]) -> int:
    """Store the lines. A fee or cashback line is filed like the ones already stored; anything else waits in Needs Review."""
    anchor = session.exec(select(Transaction).where(Transaction.account_id == account_id)
                          .order_by(Transaction.id)).first()
    if anchor is None:
        raise ValueError("The card account holds no row yet to take its document from")
    fee_node = get_node(session, "taxes-financial-costs.financial-costs.bank-card-fees")
    unsorted_node = get_node(session, "unsorted.needs-review.needs-review")
    n = 0
    for line in lines:
        stem = _stem(line.description)
        is_fee = stem.startswith("COMISSAO DISPONIBILIZACAO")
        is_cashback = stem.startswith("REEMBOLSO 123")
        node = fee_node if (is_fee or is_cashback) else unsorted_node
        merchant = None
        if is_fee or is_cashback:
            merchant = _merchant(session, "Comissão Disponibilização Cartão de Crédito" if is_fee else "Cartão 123 reembolso",
                                 fee_node.id)
        txn = Transaction(
            document_id=anchor.document_id, description=line.description, category=Category.OTHER,
            transaction_type=TransactionType.CREDIT if line.credit else TransactionType.DEBIT,
            amount=abs(line.amount), currency="EUR", paid_date=line.day, statement_period=line.day.strftime("%Y-%m"),
            account_id=account_id, merchant_id=merchant.id if merchant else None,
            nature=Nature.DISCRETIONARY if node is fee_node else None,
            external_id=hashlib.sha256(f"card|{account_id}|{line.day}|{line.amount}|{line.description}".encode()).hexdigest(),
        )
        session.add(txn)
        session.flush()
        file_transaction(session, txn, node)
        n += 1
    session.commit()
    return n


def _merchant(session: Session, name: str, category_id: int) -> Merchant:
    key = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    merchant = session.exec(select(Merchant).where(Merchant.normalized_key == key)).first()
    if merchant is None:
        merchant = Merchant(canonical_name=name, normalized_key=key, default_category=Category.OTHER,
                            default_category_id=category_id, confirmed=True)
        session.add(merchant)
        session.flush()
    return merchant


def rute_card_payments(session: Session) -> list[Transaction]:
    """Payments of Rute's card still filed as an internal transfer."""
    node_ids = [n for n in (get_node(session, "internal-transfers.between-my-accounts.top-ups-card-payments"),) if n]
    return [t for t in session.exec(select(Transaction).where(
        Transaction.description.contains(RUTE_CARD_MARK), Transaction.category_id.in_([n.id for n in node_ids]),
        Transaction.transaction_type == TransactionType.DEBIT)).all()]


def file_rute_card_payments(session: Session) -> list[Transaction]:
    node = get_node(session, RUTE_CARD_NODE)
    rows = rute_card_payments(session)
    for t in rows:
        file_transaction(session, t, node)
        t.nature = Nature.DISCRETIONARY
        session.add(t)
    session.commit()
    return rows
