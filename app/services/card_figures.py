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
from app.models.category_node import CategoryNode
from app.models.merchant import Merchant
from app.models.transaction import Category, Nature, Transaction, TransactionType
from app.services.classification_engine import _find_merchant_by_key, normalize_provider
from app.services.taxonomy import auto_fits, file_transaction, get_node

RUTE_CARD_NODE = "family.personal.rute-card-not-itemised"
RUTE_CARD_MARK = "7303599"          # the payment reference of Rute's card on the current-account statement
_LINE = re.compile(r"^\s*(\d\d)-(\d\d)-(\d{4})\s+(.+?)\s+([+-]\d[\d.]*,\d\d) EUR\s*$")


_STATEMENT_LINE = re.compile(r"^\s*(\d\d)/(\d\d)\s+\d\d/\d\d\s+(.+?)\s+(-?\d[\d.]*,\d\d) €\s*$")
_STATEMENT_TAX = re.compile(r"^\s*(I\.? ?S\.? ?\(.+?\).*?)\s+(\d[\d.]*,\d\d) €\s*$")   # stamp duty on a fee: printed without dates
_STATEMENT_DATE = re.compile(r"Data Extrato\s+(\d\d)/(\d\d)/(\d{4})")
_STATEMENT_NO = re.compile(r"Extrato n\.º\s+(\d+)")
_OPENING = re.compile(r"^\s*\d\d-\d\d\s+Saldo Anterior\s+(-?\d[\d.]*,\d\d) €", re.M)
_CLOSING = re.compile(r"Saldo em Dívida\s+(-?\d[\d.]*,\d\d) €")
_CHARGE_STEMS = ("JUROS", "C.RECUP.VAL.DIVIDA", "I. S.", "IS (")      # interest, debt-recovery fee, stamp duty
_PAYMENT_STEM = "PAG.TRANS.BANCARIA"


def _euros(text: str) -> float:
    return float(text.replace(".", "").replace(",", "."))


@dataclass(frozen=True)
class CardStatement:
    number: int
    issued: date
    opening: float
    closing: float
    lines: list["CardLine"]


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


def parse_card_statement(text: str) -> CardStatement:
    """One monthly card statement ("Extrato de Conta-Cartão"), from `pdftotext -layout`. Its lines print as
    "booked-day value-day description amount €": a purchase is a positive amount, a payment negative. Raises
    ValueError when the lines don't add up to the statement's own opening and closing balance, so a half-read
    statement is never stored."""
    issued = _STATEMENT_DATE.search(text)
    number, opening, closing = _STATEMENT_NO.search(text), _OPENING.search(text), _CLOSING.search(text)
    if not (issued and number and opening and closing):
        raise ValueError("not a card statement: its number, date or balances could not be found")
    issued_on = date(int(issued.group(3)), int(issued.group(2)), int(issued.group(1)))
    lines = []
    for raw in text.splitlines():
        tax = _STATEMENT_TAX.match(raw)
        if tax and lines:     # stamp duty belongs to the line printed just above it, on its day
            lines.append(CardLine(lines[-1].day, " ".join(tax.group(1).split()), -_euros(tax.group(2))))
            continue
        m = _STATEMENT_LINE.match(raw)
        if not m:
            continue
        d, mo, desc, amount = m.groups()
        year = issued_on.year - (1 if int(mo) > issued_on.month else 0)
        lines.append(CardLine(date(year, int(mo), int(d)), " ".join(desc.replace("*", " ").split()), -_euros(amount)))
    start, end = _euros(opening.group(1)), _euros(closing.group(1))
    if abs(start - sum(l.amount for l in lines) - end) > 0.005:
        raise ValueError(f"statement {number.group(1)}: the lines add up to {start - sum(l.amount for l in lines):.2f}, "
                         f"the statement says {end:.2f}")
    return CardStatement(int(number.group(1)), issued_on, start, end, lines)


def read_card_statement(path: str) -> CardStatement:
    return parse_card_statement(subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True,
                                               text=True, check=True).stdout)


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


_CARD_PURCHASE_PREFIX = re.compile(r"^COMPRA\s+(?:TPA|MBW|ESTRANG)\s+\d{4}\s+", re.IGNORECASE)


def known_merchant(session: Session, description: str) -> Optional[Merchant]:
    """The merchant memory for a card purchase: a CONFIRMED merchant with a category of its own that this text
    names once the card prefix ("COMPRA TPA 5990") and the store code and town are taken off."""
    key = normalize_provider(normalize_provider(_CARD_PURCHASE_PREFIX.sub("", description)))
    merchant = _find_merchant_by_key(session, key)
    if merchant is None or not merchant.confirmed or merchant.by_provider or merchant.default_category_id is None:
        return None
    return merchant


def _file_by_merchant_memory(session: Session, txn: Transaction) -> bool:
    known = known_merchant(session, txn.description)
    node = session.get(CategoryNode, known.default_category_id) if known else None
    if node is None or node.slug.startswith("unsorted"):
        return False
    txn.merchant_id = known.id
    if not auto_fits(session, txn, node):
        return False
    file_transaction(session, txn, node)
    txn.nature = txn.nature or known.default_nature
    session.add(txn)
    return True


def presort_purchases(session: Session, account_id: int) -> list[Transaction]:
    """Purchases of this card still waiting in Needs Review that a confirmed merchant can file."""
    unsorted = get_node(session, "unsorted.needs-review.needs-review")
    rows = session.exec(select(Transaction).where(Transaction.account_id == account_id,
                                                  Transaction.category_id == unsorted.id)).all()
    done = [t for t in rows if _file_by_merchant_memory(session, t)]
    session.commit()
    return done


def _stem(description: str) -> str:
    return re.sub(r"[-\s]*\d{4}$", "", description.upper().replace("CRÉDITO", "CREDITO")).strip()


def add_lines(session: Session, account_id: int, lines: list[CardLine], document_id: Optional[int] = None) -> int:
    """Store the lines. A fee, interest or cashback line is filed like the ones already stored, a payment of the
    card is an internal transfer; anything else waits in Needs Review. `document_id` is the statement they come
    from (default: the first document the card account already has)."""
    if document_id is None:
        anchor = session.exec(select(Transaction).where(Transaction.account_id == account_id)
                              .order_by(Transaction.id)).first()
        if anchor is None:
            raise ValueError("The card account holds no row yet to take its document from")
        document_id = anchor.document_id
    fee_node = get_node(session, "taxes-financial-costs.financial-costs.bank-card-fees")
    transfer_node = get_node(session, "internal-transfers.between-my-accounts.top-ups-card-payments")
    unsorted_node = get_node(session, "unsorted.needs-review.needs-review")
    n = 0
    for line in lines:
        stem = _stem(line.description)
        is_fee = stem.startswith("COMISSAO DISPONIBILIZACAO")
        is_cashback = stem.startswith("REEMBOLSO 123")
        is_charge = stem.startswith(_CHARGE_STEMS)
        is_payment = stem.startswith(_PAYMENT_STEM) and line.credit
        node = (fee_node if (is_fee or is_cashback or is_charge)
                else transfer_node if is_payment else unsorted_node)
        merchant = None
        if is_fee or is_cashback or is_charge:
            merchant = _merchant(session, "Comissão Disponibilização Cartão de Crédito" if is_fee
                                 else "Cartão 123 reembolso" if is_cashback else "Juros e encargos do cartão de crédito",
                                 fee_node.id)
        txn = Transaction(
            document_id=document_id, description=line.description, category=Category.OTHER,
            transaction_type=TransactionType.CREDIT if line.credit else TransactionType.DEBIT,
            amount=abs(line.amount), currency="EUR", paid_date=line.day, statement_period=line.day.strftime("%Y-%m"),
            account_id=account_id, merchant_id=merchant.id if merchant else None,
            nature=Nature.DISCRETIONARY if node is fee_node else None,
            external_id=hashlib.sha256(f"card|{account_id}|{line.day}|{line.amount}|{line.description}".encode()).hexdigest(),
        )
        session.add(txn)
        session.flush()
        file_transaction(session, txn, node)
        if node is unsorted_node:        # a purchase: the merchant memory files it, as for any other entry
            _file_by_merchant_memory(session, txn)
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


def unfile_rute_card_payments(session: Session) -> list[Transaction]:
    """Rute's card payments back to internal transfers, once the card's own lines are stored (they are the spending)."""
    node = get_node(session, "internal-transfers.between-my-accounts.top-ups-card-payments")
    rows = session.exec(select(Transaction).where(
        Transaction.description.contains(RUTE_CARD_MARK),
        Transaction.category_id == get_node(session, RUTE_CARD_NODE).id)).all()
    for t in rows:
        file_transaction(session, t, node)
        t.nature = None
        session.add(t)
    session.commit()
    return rows
