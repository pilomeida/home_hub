"""Card figures: the bank's card list is read as it prints (lines copied from the real page of 18 Oct 2026)."""
from datetime import date

import pytest
from sqlmodel import select

from app.models.domain import Domain
from app.models.transaction import Transaction, TransactionType
from app.services.card_figures import (
    add_lines, file_rute_card_payments, missing_lines, parse_card_list, reconcile, redate_rows, rute_card_payments,
)
from app.services.taxonomy import ensure_taxonomy, get_node
from tests.knowledge_factories import make_document

PAGE = """
Listagem Movimentos

Data Operação  Descrição  Montante (EUR)
17-10-2026  COMPRA TPA*1835 J VASCO SIMOES E FLS CARRASCAL OES  -40,04 EUR

07-10-2026  PAGAMENTO CTA_CARTÃO APP/NETBANCO  +2,34 EUR

05-09-2026  COMPRA ESTRANG*1835 AIR CAN0142335823522 FRA  -587,17 EUR

05-09-2026  COMPRA ESTRANG*1835 AIR CAN0142335823523 FRA  -587,17 EUR

02-03-2026  COMISSAO DISPONIBILIZACAO CARTAO CRÉDITO-3353  -3,00 EUR

15-12-2025  REEMBOLSO 123 PORTAGENS  +2,32 EUR

01-10-2025  COMISSAO DISPONIBILIZACAO CARTAO CRÉDITO-3353  -3,00 EUR
"""


@pytest.fixture()
def card(session):
    ensure_taxonomy(session)
    doc = make_document(session, domain=Domain.FINANCIALS, category="statement", filename="card.pdf")
    first = Transaction(document_id=doc.id, description="COMPRA ESTRANG 1835 AIR CAN", amount=587.17, account_id=3,
                        transaction_type=TransactionType.DEBIT, paid_date=date(2026, 9, 5))
    session.add(first)
    session.commit()
    return doc


def test_the_list_is_read_with_its_signs_and_decimal_commas():
    lines = parse_card_list(PAGE)
    assert len(lines) == 7
    assert (lines[0].day, lines[0].amount) == (date(2026, 10, 17), -40.04)
    assert lines[1].credit and lines[1].amount == 2.34
    assert "*" not in lines[0].description


def test_only_lines_not_held_yet_and_from_the_baseline_on_are_added(session, card):
    todo = missing_lines(session, 3, parse_card_list(PAGE))
    days = sorted((l.day, l.amount) for l in todo)
    # one of the two identical Air Canada lines is held, the other is not; 1 Oct 2025 is before the baseline
    assert days == [(date(2025, 12, 15), 2.32), (date(2026, 3, 2), -3.0), (date(2026, 9, 5), -587.17),
                    (date(2026, 10, 7), 2.34), (date(2026, 10, 17), -40.04)]


def test_added_fee_and_cashback_lines_are_filed_and_other_lines_wait_for_review(session, card):
    add_lines(session, 3, missing_lines(session, 3, parse_card_list(PAGE)))
    rows = {(t.paid_date, t.amount): t for t in session.exec(select(Transaction).where(Transaction.account_id == 3)).all()}
    fee = rows[(date(2026, 3, 2), 3.0)]
    cashback = rows[(date(2025, 12, 15), 2.32)]
    review = rows[(date(2026, 10, 17), 40.04)]
    fees_node = get_node(session, "taxes-financial-costs.financial-costs.bank-card-fees")
    assert fee.category_id == cashback.category_id == fees_node.id
    assert cashback.transaction_type == TransactionType.CREDIT and fee.transaction_type == TransactionType.DEBIT
    assert review.category_id == get_node(session, "unsorted.needs-review.needs-review").id
    # running it again adds nothing
    assert missing_lines(session, 3, parse_card_list(PAGE)) == []


def test_rutes_card_payments_count_as_spending_and_pedros_do_not(session, card):
    transfer = get_node(session, "internal-transfers.between-my-accounts.top-ups-card-payments")
    for desc, amount in (("7303599-00-PAG.CTA.CARTAO", 89.57), ("7095183-00-PAG.CTA.CARTAO", 2.56)):
        t = Transaction(document_id=card.id, description=desc, amount=amount, account_id=1, category_id=transfer.id,
                        transaction_type=TransactionType.DEBIT, paid_date=date(2026, 8, 6))
        session.add(t)
    session.commit()
    assert [t.amount for t in rute_card_payments(session)] == [89.57]
    file_rute_card_payments(session)
    rute = get_node(session, "family.personal.rute-card-not-itemised")
    rows = {t.description: t for t in session.exec(select(Transaction).where(Transaction.account_id == 1)).all()}
    assert rows["7303599-00-PAG.CTA.CARTAO"].category_id == rute.id
    assert rows["7095183-00-PAG.CTA.CARTAO"].category_id == transfer.id


def test_a_row_stamped_with_the_fetch_day_is_re_dated_not_added_twice(session, card):
    fetched = Transaction(document_id=card.id, description="PAG.TRANS.BANCARIA", amount=2.56, account_id=3,
                          transaction_type=TransactionType.CREDIT, paid_date=date(2026, 10, 6))
    session.add(fetched)
    session.commit()
    page = "06-08-2026  PAG.TRANS.BANCARIA  +2,56 EUR\n"
    todo, redates = reconcile(session, 3, parse_card_list(page))
    assert todo == [] and [(t.id, l.day) for t, l in redates] == [(fetched.id, date(2026, 8, 6))]
    redate_rows(session, redates)
    session.refresh(fetched)
    assert (fetched.paid_date, fetched.statement_period) == (date(2026, 8, 6), "2026-08")
