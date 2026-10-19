"""Card figures: the bank's card list is read as it prints (lines copied from the real page of 18 Oct 2026)."""
from datetime import date

import pytest
from sqlmodel import select

from app.models.domain import Domain
from app.models.merchant import Merchant
from app.models.transaction import Transaction, TransactionType
from app.services.card_figures import (
    add_lines, file_rute_card_payments, missing_lines, presort_purchases, parse_card_list, parse_card_statement, reconcile,
    redate_rows, rute_card_payments, unfile_rute_card_payments,
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


# Lines copied in the shape `pdftotext -layout` gives for Rute's statements of 15 Jul and 15 Oct 2026.
STATEMENT = """
 Extrato n.º       3                 Limite de Crédito       1.500,00 €             Saldo em Dívida      89,57 €
 Data Extrato      15/07/2026        Opção Pagamento            100%                Montante a Pagar     89,57 €

 Mov Valor Descrição Movimento                                                                    Montante (€)
15-07         Saldo Anterior                                                                          292,57 €
    *Movimentos de Cartão* 5415 **** **** 5990                Nome do Titular: RUTE CORREIA
01/07 01/07 COMISSAO DISPONIBILIZACAO CARTAO CRÉDITO-5990                                                3,00 €
            IS (17.3.4) 4%                                                                               0,12 €
02/07 02/07 COMPRA TPA*5990 MODELO HIPER 2640-MAFR                                                      86,45 €
06/07 06/07 PAG.TRANS.BANCARIA                                                                        -292,57 €
"""
INTEREST = """
 Extrato n.º       6                 Limite de Crédito       1.500,00 €             Saldo em Dívida     262,18 €
 Data Extrato      15/10/2026
15-10         Saldo Anterior                                                                          242,88 €
15/10 15/10 JUROS          10,80%                     Montante: 217,88 €                                  1,96 €
            I. S.(17.3.1) 4,00%                                                                           0,08 €
15/10 15/10 C.Recup.Val.Divida 4,00%                  Montante: 242,76 €                                 12,00 €
07/10 06/10 PAG.TRANS.BANCARIA                                                                           -25,00 €
            Capital                                                                                       21,88 €
            Comissões                                                                                      3,00 €
"""


def test_a_card_statement_is_read_with_its_stamp_duty_and_checked_against_its_own_balances():
    st = parse_card_statement(STATEMENT)
    assert (st.number, st.issued, st.opening, st.closing) == (3, date(2026, 7, 15), 292.57, 89.57)
    assert [(l.day, l.amount) for l in st.lines] == [
        (date(2026, 7, 1), -3.0), (date(2026, 7, 1), -0.12), (date(2026, 7, 2), -86.45), (date(2026, 7, 6), 292.57)]
    assert st.lines[1].description == "IS (17.3.4) 4%" and "*" not in st.lines[2].description


def test_a_statement_whose_lines_do_not_add_up_is_refused_not_half_stored():
    with pytest.raises(ValueError, match="statement 6"):
        parse_card_statement(INTEREST)        # most of this statement's lines are left out of the fixture


def test_statement_lines_are_filed_charges_as_costs_payments_as_transfers_purchases_for_review(session, card):
    st = parse_card_statement(STATEMENT)
    doc = make_document(session, domain=Domain.FINANCIALS, category="statement", filename="s3.pdf")
    assert add_lines(session, 4, st.lines, document_id=doc.id) == 4
    rows = {(t.paid_date, round(t.amount, 2)): t for t in session.exec(select(Transaction).where(Transaction.account_id == 4)).all()}
    costs = get_node(session, "taxes-financial-costs.financial-costs.bank-card-fees")
    assert rows[(date(2026, 7, 1), 3.0)].category_id == rows[(date(2026, 7, 1), 0.12)].category_id == costs.id
    assert rows[(date(2026, 7, 6), 292.57)].category_id == get_node(
        session, "internal-transfers.between-my-accounts.top-ups-card-payments").id
    assert rows[(date(2026, 7, 2), 86.45)].category_id == get_node(session, "unsorted.needs-review.needs-review").id
    assert {t.document_id for t in rows.values()} == {doc.id}
    # the card's own balance: what it owes is the statement's closing balance
    owed = sum(t.amount if t.transaction_type == TransactionType.DEBIT else -t.amount for t in rows.values())
    assert round(owed, 2) == round(st.closing - st.opening, 2)


def test_rutes_not_itemised_payments_go_back_to_transfers_once_the_card_lines_exist(session, card):
    transfer = get_node(session, "internal-transfers.between-my-accounts.top-ups-card-payments")
    t = Transaction(document_id=card.id, description="7303599-00-PAG.CTA.CARTAO", amount=89.57, account_id=1,
                    category_id=transfer.id, transaction_type=TransactionType.DEBIT, paid_date=date(2026, 8, 6))
    session.add(t)
    session.commit()
    file_rute_card_payments(session)
    assert [r.id for r in unfile_rute_card_payments(session)] == [t.id]
    session.refresh(t)
    assert t.category_id == transfer.id and t.nature is None
    assert rute_card_payments(session) == [t]


def test_card_purchases_are_filed_by_a_confirmed_merchant_and_the_rest_wait_for_review(session, card):
    ensure_taxonomy(session)
    supermarket = get_node(session, "food.groceries.supermarket")
    session.add(Merchant(canonical_name="Modelo Hiper", normalized_key="modelo hiper", confirmed=True,
                         default_category_id=supermarket.id))
    session.add(Merchant(canonical_name="Faculdade", normalized_key="faculdade medicina lisboa", confirmed=False,
                         default_category_id=get_node(session, "family.education.school-fees").id))
    session.commit()
    lines = parse_card_list(
        "02-07-2026  COMPRA TPA*5990 MODELO HIPER 2640-MAFR  -86,45 EUR\n"
        "24-07-2026  COMPRA TPA*5990 FACULDADE MEDICINA LISBOA  -5,00 EUR\n")
    add_lines(session, 4, lines, document_id=card.id)
    rows = {t.amount: t for t in session.exec(select(Transaction).where(Transaction.account_id == 4)).all()}
    assert rows[86.45].category_id == supermarket.id and rows[86.45].merchant_id is not None
    assert rows[5.0].category_id == get_node(session, "unsorted.needs-review.needs-review").id   # not confirmed
    assert presort_purchases(session, 4) == []        # nothing more it may file
